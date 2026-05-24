import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add contacts.out_path_hash_mode and backfill legacy rows.

    Historical databases predate multibyte routing support. Backfill rules:
    - contacts with last_path_len = -1 are flood routes -> out_path_hash_mode = -1
    - all other existing contacts default to 0 (1-byte legacy hop identifiers)
    """
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    column_cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns = {row[1] for row in await column_cursor.fetchall()}

    added_column = False

    try:
        await conn.execute(
            "ALTER TABLE contacts ADD COLUMN out_path_hash_mode INTEGER NOT NULL DEFAULT 0"
        )
        added_column = True
        logger.debug("Added out_path_hash_mode to contacts table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("contacts.out_path_hash_mode already exists, skipping add")
        else:
            raise

    if "last_path_len" not in columns:
        await conn.commit()
        return

    if added_column:
        await conn.execute(
            """
            UPDATE contacts
            SET out_path_hash_mode = CASE
                WHEN last_path_len = -1 THEN -1
                ELSE 0
            END
            """
        )
    else:
        await conn.execute(
            """
            UPDATE contacts
            SET out_path_hash_mode = CASE
                WHEN last_path_len = -1 THEN -1
                ELSE 0
            END
            WHERE out_path_hash_mode NOT IN (-1, 0, 1, 2)
               OR (last_path_len = -1 AND out_path_hash_mode != -1)
            """
        )
    await conn.commit()
