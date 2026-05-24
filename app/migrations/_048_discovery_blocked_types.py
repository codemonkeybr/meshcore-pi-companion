import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add discovery_blocked_types column to app_settings.

    Stores a JSON array of integer contact type codes (1=Client, 2=Repeater,
    3=Room, 4=Sensor) whose advertisements should not create new contacts.
    Empty list means all types are accepted.
    """
    try:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN discovery_blocked_types TEXT DEFAULT '[]'"
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "duplicate column" in error_msg:
            logger.debug("discovery_blocked_types column already exists, skipping")
        elif "no such table" in error_msg:
            logger.debug("app_settings table not ready, skipping discovery_blocked_types migration")
        else:
            raise
    await conn.commit()
