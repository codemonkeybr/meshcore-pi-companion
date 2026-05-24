import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Rename repeater_advert_paths to contact_advert_paths with column
    repeater_key -> public_key.

    Uses table rebuild since ALTER TABLE RENAME COLUMN may not be available
    in older SQLite versions.
    """
    # Check if old table exists
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='repeater_advert_paths'"
    )
    if not await cursor.fetchone():
        # Already renamed or doesn't exist — ensure new table exists
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_advert_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                path_hex TEXT NOT NULL,
                path_len INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                heard_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(public_key, path_hex, path_len),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
            "ON contact_advert_paths(public_key, last_seen DESC)"
        )
        await conn.commit()
        logger.debug("contact_advert_paths already exists or old table missing, skipping rename")
        return

    # Create new table (IF NOT EXISTS in case SCHEMA already created it)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_advert_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_key TEXT NOT NULL,
            path_hex TEXT NOT NULL,
            path_len INTEGER NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            heard_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(public_key, path_hex, path_len),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key)
        )
        """
    )

    # Copy data (INSERT OR IGNORE in case of duplicates)
    await conn.execute(
        """
        INSERT OR IGNORE INTO contact_advert_paths (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
        SELECT repeater_key, path_hex, path_len, first_seen, last_seen, heard_count
        FROM repeater_advert_paths
        """
    )

    # Drop old table
    await conn.execute("DROP TABLE repeater_advert_paths")

    # Create index
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
        "ON contact_advert_paths(public_key, last_seen DESC)"
    )

    await conn.commit()
    logger.info("Renamed repeater_advert_paths to contact_advert_paths")
