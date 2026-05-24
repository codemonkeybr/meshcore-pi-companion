import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add indexes used by the statistics endpoint's time-windowed scans."""
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await cursor.fetchall()}

    if "raw_packets" in tables:
        cursor = await conn.execute("PRAGMA table_info(raw_packets)")
        raw_packet_columns = {row[1] for row in await cursor.fetchall()}
        if "timestamp" in raw_packet_columns:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_raw_packets_timestamp ON raw_packets(timestamp)"
            )

    if "contacts" in tables:
        cursor = await conn.execute("PRAGMA table_info(contacts)")
        contact_columns = {row[1] for row in await cursor.fetchall()}
        if {"type", "last_seen"}.issubset(contact_columns):
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_contacts_type_last_seen ON contacts(type, last_seen)"
            )

    if "messages" in tables:
        cursor = await conn.execute("PRAGMA table_info(messages)")
        message_columns = {row[1] for row in await cursor.fetchall()}
        if {"type", "received_at", "conversation_key"}.issubset(message_columns):
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_type_received_conversation
                ON messages(type, received_at, conversation_key)
                """
            )
    await conn.commit()
