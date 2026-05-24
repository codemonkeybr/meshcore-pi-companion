import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add experimental_channel_double_send column to app_settings table.

    When enabled, channel sends perform an immediate byte-perfect duplicate send
    using the same timestamp bytes.
    """
    try:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN experimental_channel_double_send INTEGER DEFAULT 0"
        )
        logger.debug("Added experimental_channel_double_send column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("experimental_channel_double_send column already exists, skipping")
        else:
            raise

    await conn.commit()
