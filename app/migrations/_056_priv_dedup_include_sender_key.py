import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add sender_key to the incoming PRIV dedup index.

    Room-server posts are stored as PRIV messages sharing one conversation_key
    (the room contact).  Without sender_key in the uniqueness constraint, two
    different room participants sending identical text in the same clock second
    collide and the second message is silently dropped.

    Adding COALESCE(sender_key, '') is strictly more permissive — no existing
    rows can conflict — so the migration only needs to rebuild the index.
    """
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    # The index references type, conversation_key, sender_timestamp, outgoing,
    # and sender_key.  Some migration tests create minimal messages tables that
    # lack these columns.  Skip gracefully when the schema is too old.
    col_cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    required = {"type", "conversation_key", "sender_timestamp", "outgoing", "sender_key"}
    if not required.issubset(columns):
        await conn.commit()
        return

    await conn.execute("DROP INDEX IF EXISTS idx_messages_incoming_priv_dedup")
    await conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_incoming_priv_dedup
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0),
                       COALESCE(sender_key, ''))
           WHERE type = 'PRIV' AND outgoing = 0"""
    )
    await conn.commit()
