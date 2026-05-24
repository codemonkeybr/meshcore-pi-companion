import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Restrict the message dedup index to channel messages."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required_columns = {"type", "conversation_key", "text", "sender_timestamp"}
    if not required_columns.issubset(columns):
        logger.debug("messages table missing dedup-index columns, skipping migration 43")
        await conn.commit()
        return

    await conn.execute("DROP INDEX IF EXISTS idx_messages_dedup_null_safe")
    await conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup_null_safe
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))
           WHERE type = 'CHAN'"""
    )
    await conn.commit()
