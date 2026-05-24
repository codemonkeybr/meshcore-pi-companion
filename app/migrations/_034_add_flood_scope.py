import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add flood_scope column to app_settings for outbound region tagging.

    Empty string means disabled (no scope set, messages sent unscoped).
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN flood_scope TEXT DEFAULT ''")
        await conn.commit()
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("flood_scope column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping flood_scope migration")
        else:
            raise
