import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Fix NULL sender_timestamp values and add null-safe dedup index.

    1. Set sender_timestamp = received_at for any messages with NULL sender_timestamp
    2. Create a null-safe unique index as belt-and-suspenders protection
    """
    # Check if messages table exists
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if not await cursor.fetchone():
        logger.debug("messages table does not exist yet, skipping NULL sender_timestamp fix")
        await conn.commit()
        return

    # Backfill NULL sender_timestamps with received_at
    cursor = await conn.execute(
        "UPDATE messages SET sender_timestamp = received_at WHERE sender_timestamp IS NULL"
    )
    if cursor.rowcount > 0:
        logger.info("Backfilled %d messages with NULL sender_timestamp", cursor.rowcount)

    # Try to create null-safe dedup index (may fail if existing duplicates exist)
    try:
        await conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup_null_safe
               ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
        )
        logger.debug("Created null-safe dedup index")
    except aiosqlite.IntegrityError:
        logger.warning(
            "Could not create null-safe dedup index due to existing duplicates - "
            "the application-level dedup will handle these"
        )

    await conn.commit()
