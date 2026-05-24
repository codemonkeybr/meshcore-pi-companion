import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Enforce minimum 1-hour advert interval.

    Any advert_interval between 1 and 3599 is clamped up to 3600 (1 hour).
    Zero (disabled) is left unchanged.
    """
    # Guard: app_settings table may not exist if running against a very old schema
    # (it's created in migration 9). The UPDATE is harmless if the table exists
    # but has no rows, but will error if the table itself is missing.
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if await cursor.fetchone() is None:
        logger.debug("app_settings table does not exist yet, skipping advert_interval clamp")
        return

    await conn.execute(
        "UPDATE app_settings SET advert_interval = 3600 WHERE advert_interval > 0 AND advert_interval < 3600"
    )
    await conn.commit()
    logger.debug("Clamped advert_interval to minimum 3600 seconds")
