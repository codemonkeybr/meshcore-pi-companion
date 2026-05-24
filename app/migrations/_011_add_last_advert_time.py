import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add last_advert_time column to app_settings table.

    This tracks when the last advertisement was sent, ensuring we never
    advertise faster than the configured advert_interval.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN last_advert_time INTEGER DEFAULT 0")
        logger.debug("Added last_advert_time column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("last_advert_time column already exists, skipping")
        else:
            raise

    await conn.commit()
