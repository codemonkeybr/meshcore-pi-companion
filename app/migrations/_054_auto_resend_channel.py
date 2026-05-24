import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add auto_resend_channel boolean column to app_settings."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "auto_resend_channel" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN auto_resend_channel INTEGER DEFAULT 0"
        )
        await conn.commit()
