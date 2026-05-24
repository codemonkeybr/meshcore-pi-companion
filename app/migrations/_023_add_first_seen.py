import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add first_seen column to contacts table.

    Backfill strategy:
    1. Set first_seen = last_seen for all contacts (baseline).
    2. For contacts with PRIV messages, set first_seen = MIN(messages.received_at)
       if that timestamp is earlier.
    """
    # Guard: skip if contacts table doesn't exist (e.g. partial test schemas)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if not await cursor.fetchone():
        return

    try:
        await conn.execute("ALTER TABLE contacts ADD COLUMN first_seen INTEGER")
        logger.debug("Added first_seen to contacts table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("contacts.first_seen already exists, skipping")
        else:
            raise

    # Baseline: set first_seen = last_seen for all contacts
    # Check if last_seen column exists (should in production, may not in minimal test schemas)
    cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "last_seen" in columns:
        await conn.execute("UPDATE contacts SET first_seen = last_seen WHERE first_seen IS NULL")

    # Refine: for contacts with PRIV messages, use earliest message timestamp if earlier
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cursor.fetchone():
        await conn.execute(
            """
            UPDATE contacts SET first_seen = (
                SELECT MIN(m.received_at) FROM messages m
                WHERE m.type = 'PRIV' AND m.conversation_key = contacts.public_key
            )
            WHERE EXISTS (
                SELECT 1 FROM messages m
                WHERE m.type = 'PRIV' AND m.conversation_key = contacts.public_key
                  AND m.received_at < COALESCE(contacts.first_seen, 9999999999)
            )
            """
        )

    await conn.commit()
    logger.debug("Added and backfilled first_seen column")
