import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(
    conn: aiosqlite.Connection,
) -> None:
    """Rebuild contact_advert_paths so uniqueness includes path_len.

    Multi-byte routing can produce the same path_hex bytes with a different hop count,
    which changes the hop boundaries and therefore the semantic next-hop identity.
    """
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contact_advert_paths'"
    )
    if await cursor.fetchone() is None:
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
        await conn.execute("DROP INDEX IF EXISTS idx_contact_advert_paths_recent")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
            "ON contact_advert_paths(public_key, last_seen DESC)"
        )
        await conn.commit()
        return

    await conn.execute(
        """
        CREATE TABLE contact_advert_paths_new (
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
        """
        INSERT INTO contact_advert_paths_new
            (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
        SELECT
            public_key,
            path_hex,
            path_len,
            MIN(first_seen),
            MAX(last_seen),
            SUM(heard_count)
        FROM contact_advert_paths
        GROUP BY public_key, path_hex, path_len
        """
    )

    await conn.execute("DROP TABLE contact_advert_paths")
    await conn.execute("ALTER TABLE contact_advert_paths_new RENAME TO contact_advert_paths")
    await conn.execute("DROP INDEX IF EXISTS idx_contact_advert_paths_recent")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent "
        "ON contact_advert_paths(public_key, last_seen DESC)"
    )
    await conn.commit()
