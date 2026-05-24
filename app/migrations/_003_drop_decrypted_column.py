import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Drop the decrypted column and update indexes.

    The decrypted column is redundant with message_id - a packet is decrypted
    iff message_id IS NOT NULL. We replace the decrypted index with a message_id index.

    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    we silently skip the column drop but still update the index.
    """
    # First, drop the old index on decrypted (safe even if it doesn't exist)
    try:
        await conn.execute("DROP INDEX IF EXISTS idx_raw_packets_decrypted")
        logger.debug("Dropped idx_raw_packets_decrypted index")
    except aiosqlite.OperationalError:
        pass  # Index didn't exist

    # Create new index on message_id for efficient undecrypted packet queries
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_packets_message_id ON raw_packets(message_id)"
        )
        logger.debug("Created idx_raw_packets_message_id index")
    except aiosqlite.OperationalError as e:
        if "already exists" not in str(e).lower():
            raise

    # Try to drop the decrypted column
    try:
        await conn.execute("ALTER TABLE raw_packets DROP COLUMN decrypted")
        logger.debug("Dropped decrypted from raw_packets table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("raw_packets.decrypted already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, decrypted column will remain")
        else:
            raise

    await conn.commit()
