import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add payload_hash column to raw_packets for deduplication.

    This column stores the SHA-256 hash of the packet payload (excluding routing/path info).
    It will be used with a unique index to prevent duplicate packets from being stored.
    """
    try:
        await conn.execute("ALTER TABLE raw_packets ADD COLUMN payload_hash TEXT")
        logger.debug("Added payload_hash column to raw_packets table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("raw_packets.payload_hash already exists, skipping")
        else:
            raise

    await conn.commit()
