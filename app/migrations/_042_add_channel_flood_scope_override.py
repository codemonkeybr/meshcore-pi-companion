import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add nullable per-channel flood-scope override column."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='channels'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    try:
        await conn.execute("ALTER TABLE channels ADD COLUMN flood_scope_override TEXT")
        logger.debug("Added flood_scope_override to channels table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("channels.flood_scope_override already exists, skipping")
        else:
            raise

    await conn.commit()
