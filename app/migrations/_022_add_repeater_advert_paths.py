import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Create table for recent unique advert paths per repeater.

    This keeps path diversity for repeater advertisements without changing the
    existing payload-hash raw packet dedup policy.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS repeater_advert_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repeater_key TEXT NOT NULL,
            path_hex TEXT NOT NULL,
            path_len INTEGER NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            heard_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(repeater_key, path_hex),
            FOREIGN KEY (repeater_key) REFERENCES contacts(public_key)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repeater_advert_paths_recent "
        "ON repeater_advert_paths(repeater_key, last_seen DESC)"
    )
    await conn.commit()
    logger.debug("Ensured repeater_advert_paths table and indexes exist")
