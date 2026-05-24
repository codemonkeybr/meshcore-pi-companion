import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Backfill contacts.first_seen from contact_advert_paths where advert path
    first_seen is earlier than the contact's current first_seen.
    """
    # Guard: skip if either table doesn't exist
    for table in ("contacts", "contact_advert_paths"):
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not await cursor.fetchone():
            return

    await conn.execute(
        """
        UPDATE contacts SET first_seen = (
            SELECT MIN(cap.first_seen) FROM contact_advert_paths cap
            WHERE cap.public_key = contacts.public_key
        )
        WHERE EXISTS (
            SELECT 1 FROM contact_advert_paths cap
            WHERE cap.public_key = contacts.public_key
              AND cap.first_seen < COALESCE(contacts.first_seen, 9999999999)
        )
        """
    )

    await conn.commit()
    logger.debug("Backfilled first_seen from contact_advert_paths")
