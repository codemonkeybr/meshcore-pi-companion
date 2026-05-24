import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add last_read_at column to contacts and channels tables.

    This enables server-side read state tracking, replacing the localStorage
    approach for consistent read state across devices.

    ALTER TABLE ADD COLUMN is safe - it preserves existing data and handles
    the "column already exists" case gracefully.
    """
    # Add to contacts table
    try:
        await conn.execute("ALTER TABLE contacts ADD COLUMN last_read_at INTEGER")
        logger.debug("Added last_read_at to contacts table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("contacts.last_read_at already exists, skipping")
        else:
            raise

    # Add to channels table
    try:
        await conn.execute("ALTER TABLE channels ADD COLUMN last_read_at INTEGER")
        logger.debug("Added last_read_at to channels table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("channels.last_read_at already exists, skipping")
        else:
            raise

    await conn.commit()
