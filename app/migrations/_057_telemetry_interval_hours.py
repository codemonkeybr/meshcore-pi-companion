import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add telemetry_interval_hours integer column to app_settings."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "telemetry_interval_hours" not in columns:
        # Default to 8 hours, matching the previous hard-coded interval
        # so existing users see no behavior change until they opt in.
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN telemetry_interval_hours INTEGER DEFAULT 8"
        )
        await conn.commit()
