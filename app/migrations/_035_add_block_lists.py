import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add blocked_keys and blocked_names columns to app_settings.

    These store JSON arrays of blocked public keys and display names.
    Blocking hides messages from the UI but does not affect MQTT or bots.
    """
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN blocked_keys TEXT DEFAULT '[]'")
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("blocked_keys column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping blocked_keys migration")
        else:
            raise

    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN blocked_names TEXT DEFAULT '[]'")
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("blocked_names column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping blocked_names migration")
        else:
            raise

    await conn.commit()
