import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Move favorites from app_settings JSON blob to per-entity boolean columns.

    1. Add ``favorite`` column to contacts and channels tables.
    2. Backfill from the ``app_settings.favorites`` JSON array.
    3. Drop the ``favorites`` column from app_settings.
    """
    import json as _json

    # --- Add columns ---
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}
    for table in ("contacts", "channels"):
        if table not in existing_tables:
            continue
        col_cursor = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "favorite" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN favorite INTEGER DEFAULT 0")
    await conn.commit()

    # --- Backfill from JSON ---
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    settings_columns = {row[1] for row in await col_cursor.fetchall()}
    if "favorites" not in settings_columns:
        await conn.commit()
        return

    cursor = await conn.execute("SELECT favorites FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            favorites = _json.loads(row[0])
        except (ValueError, TypeError):
            favorites = []

        contact_keys = []
        channel_keys = []
        for fav in favorites:
            if not isinstance(fav, dict):
                continue
            fav_type = fav.get("type")
            fav_id = fav.get("id")
            if not fav_id:
                continue
            if fav_type == "contact":
                contact_keys.append(fav_id)
            elif fav_type == "channel":
                channel_keys.append(fav_id)

        if contact_keys:
            placeholders = ",".join("?" for _ in contact_keys)
            await conn.execute(
                f"UPDATE contacts SET favorite = 1 WHERE public_key IN ({placeholders})",
                contact_keys,
            )
        if channel_keys:
            placeholders = ",".join("?" for _ in channel_keys)
            await conn.execute(
                f"UPDATE channels SET favorite = 1 WHERE key IN ({placeholders})",
                channel_keys,
            )
        if contact_keys or channel_keys:
            logger.info(
                "Backfilled %d contact favorite(s) and %d channel favorite(s) from app_settings",
                len(contact_keys),
                len(channel_keys),
            )
    await conn.commit()

    # --- Drop the JSON column ---
    try:
        await conn.execute("ALTER TABLE app_settings DROP COLUMN favorites")
        await conn.commit()
    except Exception as e:
        error_msg = str(e).lower()
        if "syntax error" in error_msg or "drop column" in error_msg:
            logger.debug("SQLite doesn't support DROP COLUMN; favorites column will remain unused")
            await conn.commit()
        else:
            raise
