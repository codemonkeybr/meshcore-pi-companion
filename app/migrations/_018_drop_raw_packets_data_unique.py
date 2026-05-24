import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Drop the UNIQUE constraint on raw_packets.data via table rebuild.

    This constraint creates a large autoindex (~30 MB on a 340K-row database) that
    stores a complete copy of every raw packet BLOB in a B-tree. Deduplication is
    already handled by the unique index on payload_hash, making the data UNIQUE
    constraint pure storage overhead.

    Requires table recreation since SQLite doesn't support DROP CONSTRAINT.
    """
    # Check if the autoindex exists (indicates UNIQUE constraint on data)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='sqlite_autoindex_raw_packets_1'"
    )
    if not await cursor.fetchone():
        logger.debug("raw_packets.data UNIQUE constraint already absent, skipping rebuild")
        await conn.commit()
        return

    logger.info("Rebuilding raw_packets table to remove UNIQUE(data) constraint...")

    # Get current columns from the existing table
    cursor = await conn.execute("PRAGMA table_info(raw_packets)")
    old_cols = {col[1] for col in await cursor.fetchall()}

    # Target schema without UNIQUE on data
    await conn.execute("""
        CREATE TABLE raw_packets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            data BLOB NOT NULL,
            message_id INTEGER,
            payload_hash TEXT,
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )
    """)

    # Copy only columns that exist in both old and new tables
    new_cols = {"id", "timestamp", "data", "message_id", "payload_hash"}
    copy_cols = ", ".join(sorted(c for c in new_cols if c in old_cols))

    await conn.execute(
        f"INSERT INTO raw_packets_new ({copy_cols}) SELECT {copy_cols} FROM raw_packets"
    )
    await conn.execute("DROP TABLE raw_packets")
    await conn.execute("ALTER TABLE raw_packets_new RENAME TO raw_packets")

    # Recreate indexes
    await conn.execute(
        "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
    )
    await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

    await conn.commit()
    logger.info("raw_packets table rebuilt without UNIQUE(data) constraint")
