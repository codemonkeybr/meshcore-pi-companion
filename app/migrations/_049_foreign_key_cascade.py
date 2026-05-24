import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Rebuild FK tables with CASCADE/SET NULL and clean orphaned rows.

    SQLite cannot ALTER existing FK constraints, so each table is rebuilt.
    Orphaned child rows are cleaned up before the rebuild to ensure the
    INSERT...SELECT into the new table (which has enforced FKs) succeeds.
    """
    import shutil
    from pathlib import Path

    # Back up the database before table rebuilds (skip for in-memory DBs).
    cursor = await conn.execute("PRAGMA database_list")
    db_row = await cursor.fetchone()
    db_path = db_row[2] if db_row else ""
    if db_path and db_path != ":memory:" and Path(db_path).exists():
        backup_path = db_path + ".pre-fk-migration.bak"
        for suffix in ("", "-wal", "-shm"):
            src = Path(db_path + suffix)
            if src.exists():
                shutil.copy2(str(src), backup_path + suffix)
        logger.info("Database backed up to %s before FK migration", backup_path)

    # --- Phase 1: clean orphans (guard each table's existence) ---
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}

    if "contact_advert_paths" in existing_tables and "contacts" in existing_tables:
        await conn.execute(
            "DELETE FROM contact_advert_paths "
            "WHERE public_key NOT IN (SELECT public_key FROM contacts)"
        )
    if "contact_name_history" in existing_tables and "contacts" in existing_tables:
        await conn.execute(
            "DELETE FROM contact_name_history "
            "WHERE public_key NOT IN (SELECT public_key FROM contacts)"
        )
    if "raw_packets" in existing_tables and "messages" in existing_tables:
        # Guard: message_id column may not exist on very old schemas
        col_cursor = await conn.execute("PRAGMA table_info(raw_packets)")
        raw_cols = {row[1] for row in await col_cursor.fetchall()}
        if "message_id" in raw_cols:
            await conn.execute(
                "UPDATE raw_packets SET message_id = NULL WHERE message_id IS NOT NULL "
                "AND message_id NOT IN (SELECT id FROM messages)"
            )
    await conn.commit()
    logger.debug("Cleaned orphaned child rows before FK rebuild")

    # --- Phase 2: rebuild raw_packets with ON DELETE SET NULL ---
    # Skip if raw_packets doesn't have message_id (pre-migration-18 schema)
    raw_has_message_id = False
    if "raw_packets" in existing_tables:
        col_cursor2 = await conn.execute("PRAGMA table_info(raw_packets)")
        raw_has_message_id = "message_id" in {row[1] for row in await col_cursor2.fetchall()}

    if raw_has_message_id:
        # Dynamically build column list based on what the old table actually has,
        # since very old schemas may lack payload_hash (added in migration 28).
        col_cursor3 = await conn.execute("PRAGMA table_info(raw_packets)")
        old_cols = [row[1] for row in await col_cursor3.fetchall()]

        new_col_defs = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "timestamp INTEGER NOT NULL",
            "data BLOB NOT NULL",
            "message_id INTEGER",
        ]
        copy_cols = ["id", "timestamp", "data", "message_id"]
        if "payload_hash" in old_cols:
            new_col_defs.append("payload_hash BLOB")
            copy_cols.append("payload_hash")
        new_col_defs.append("FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL")

        cols_sql = ", ".join(new_col_defs)
        copy_sql = ", ".join(copy_cols)
        await conn.execute(f"CREATE TABLE raw_packets_fk ({cols_sql})")
        await conn.execute(
            f"INSERT INTO raw_packets_fk ({copy_sql}) SELECT {copy_sql} FROM raw_packets"
        )
        await conn.execute("DROP TABLE raw_packets")
        await conn.execute("ALTER TABLE raw_packets_fk RENAME TO raw_packets")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_packets_message_id ON raw_packets(message_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_packets_timestamp ON raw_packets(timestamp)"
        )
        if "payload_hash" in old_cols:
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
        await conn.commit()
        logger.debug("Rebuilt raw_packets with ON DELETE SET NULL")

    # --- Phase 3: rebuild contact_advert_paths with ON DELETE CASCADE ---
    if "contact_advert_paths" in existing_tables:
        await conn.execute(
            """
            CREATE TABLE contact_advert_paths_fk (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                path_hex TEXT NOT NULL,
                path_len INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                heard_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(public_key, path_hex, path_len),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            "INSERT INTO contact_advert_paths_fk (id, public_key, path_hex, path_len, first_seen, last_seen, heard_count) "
            "SELECT id, public_key, path_hex, path_len, first_seen, last_seen, heard_count FROM contact_advert_paths"
        )
        await conn.execute("DROP TABLE contact_advert_paths")
        await conn.execute("ALTER TABLE contact_advert_paths_fk RENAME TO contact_advert_paths")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
            "ON contact_advert_paths(public_key, last_seen DESC)"
        )
        await conn.commit()
        logger.debug("Rebuilt contact_advert_paths with ON DELETE CASCADE")

    # --- Phase 4: rebuild contact_name_history with ON DELETE CASCADE ---
    if "contact_name_history" in existing_tables:
        await conn.execute(
            """
            CREATE TABLE contact_name_history_fk (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                UNIQUE(public_key, name),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            "INSERT INTO contact_name_history_fk (id, public_key, name, first_seen, last_seen) "
            "SELECT id, public_key, name, first_seen, last_seen FROM contact_name_history"
        )
        await conn.execute("DROP TABLE contact_name_history")
        await conn.execute("ALTER TABLE contact_name_history_fk RENAME TO contact_name_history")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contact_name_history_key "
            "ON contact_name_history(public_key, last_seen DESC)"
        )
        await conn.commit()
        logger.debug("Rebuilt contact_name_history with ON DELETE CASCADE")
