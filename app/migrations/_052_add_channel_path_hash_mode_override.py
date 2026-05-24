import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add nullable per-channel path hash mode override column."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "channels" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    try:
        await conn.execute("ALTER TABLE channels ADD COLUMN path_hash_mode_override INTEGER")
        await conn.commit()
    except Exception as e:
        if "duplicate column" in str(e).lower():
            await conn.commit()
        else:
            raise
