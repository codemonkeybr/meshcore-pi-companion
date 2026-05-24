import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add bot_enabled and bot_code columns to app_settings table.

    This enables user-defined Python code to be executed when messages are received,
    allowing for custom bot responses.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bot_enabled INTEGER DEFAULT 0")
        logger.debug("Added bot_enabled column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bot_enabled column already exists, skipping")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bot_code TEXT DEFAULT ''")
        logger.debug("Added bot_code column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bot_code column already exists, skipping")
        else:
            raise

    await conn.commit()
