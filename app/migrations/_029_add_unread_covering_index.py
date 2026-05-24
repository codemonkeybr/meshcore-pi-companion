import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add a covering index for the unread counts query.

    The /api/read-state/unreads endpoint runs three queries against messages.
    The last-message-times query (GROUP BY type, conversation_key + MAX(received_at))
    was doing a full table scan. This covering index lets SQLite resolve the
    grouping and MAX entirely from the index without touching the table.
    It also improves the unread count queries which filter on outgoing and received_at.
    """
    # Guard: table or columns may not exist in partial-schema test setups
    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    required = {"type", "conversation_key", "outgoing", "received_at"}
    if required <= columns:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_unread_covering "
            "ON messages(type, conversation_key, outgoing, received_at)"
        )
    await conn.commit()
