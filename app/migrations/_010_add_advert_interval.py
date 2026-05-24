import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add advert_interval column to app_settings table.

    This enables configurable periodic advertisement interval (default 0 = disabled).
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN advert_interval INTEGER DEFAULT 0")
        logger.debug("Added advert_interval column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("advert_interval column already exists, skipping")
        else:
            raise

    await conn.commit()
