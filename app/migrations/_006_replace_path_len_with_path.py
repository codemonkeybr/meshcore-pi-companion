import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Replace path_len INTEGER column with path TEXT column in messages table.

    The path column stores the hex-encoded routing path bytes. Path length can
    be derived from the hex string (2 chars per byte = 1 hop).

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip the drop (the column will remain but is unused).
    """
    # First, add the new path column
    try:
        await conn.execute("ALTER TABLE messages ADD COLUMN path TEXT")
        logger.debug("Added path column to messages table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("messages.path already exists, skipping")
        else:
            raise

    # Try to drop the old path_len column
    try:
        await conn.execute("ALTER TABLE messages DROP COLUMN path_len")
        logger.debug("Dropped path_len from messages table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("messages.path_len already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, path_len column will remain")
        else:
            raise

    await conn.commit()
