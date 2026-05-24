import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Create contact_name_history table and seed with current contact names.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_name_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_key TEXT NOT NULL,
            name TEXT NOT NULL,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            UNIQUE(public_key, name),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_name_history_key "
        "ON contact_name_history(public_key, last_seen DESC)"
    )

    # Seed: one row per contact from current data (skip if contacts table doesn't exist
    # or lacks needed columns)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if await cursor.fetchone():
        cursor = await conn.execute("PRAGMA table_info(contacts)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "name" in cols and "public_key" in cols:
            first_seen_expr = "first_seen" if "first_seen" in cols else "0"
            last_seen_expr = "last_seen" if "last_seen" in cols else "0"
            await conn.execute(
                f"""
                INSERT OR IGNORE INTO contact_name_history (public_key, name, first_seen, last_seen)
                SELECT public_key, name,
                       COALESCE({first_seen_expr}, {last_seen_expr}, 0),
                       COALESCE({last_seen_expr}, 0)
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                """
            )

    await conn.commit()
    logger.debug("Created contact_name_history table and seeded from contacts")
