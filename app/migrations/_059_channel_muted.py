import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add muted column to channels table."""
    table_check = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='channels'"
    )
    if not await table_check.fetchone():
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(channels)")
    columns = {row[1] for row in await cursor.fetchall()}

    if "muted" not in columns:
        await conn.execute("ALTER TABLE channels ADD COLUMN muted INTEGER DEFAULT 0")

    await conn.commit()
