import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add a composite index for message pagination and drop the now-redundant
    idx_messages_conversation.

    The pagination query (ORDER BY received_at DESC, id DESC LIMIT N) hits a
    temp B-tree sort without this index. With it, SQLite walks the index in
    order and stops after N rows — critical for channels with 30K+ messages.

    idx_messages_conversation(type, conversation_key) is a strict prefix of
    both this index and idx_messages_unread_covering, so SQLite never picks it.
    Dropping it saves ~6 MB and one index to maintain per INSERT.
    """
    # Guard: table or columns may not exist in partial-schema test setups
    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required = {"type", "conversation_key", "received_at", "id"}
    if required <= columns:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_pagination "
            "ON messages(type, conversation_key, received_at DESC, id DESC)"
        )
        await conn.execute("DROP INDEX IF EXISTS idx_messages_conversation")
    await conn.commit()
