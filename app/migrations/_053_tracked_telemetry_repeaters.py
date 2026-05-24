import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add tracked_telemetry_repeaters JSON list column to app_settings."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "tracked_telemetry_repeaters" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN tracked_telemetry_repeaters TEXT DEFAULT '[]'"
        )
        await conn.commit()
