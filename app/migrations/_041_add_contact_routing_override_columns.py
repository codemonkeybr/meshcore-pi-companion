import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add nullable routing-override columns to contacts."""
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    for column_name, column_type in (
        ("route_override_path", "TEXT"),
        ("route_override_len", "INTEGER"),
        ("route_override_hash_mode", "INTEGER"),
    ):
        try:
            await conn.execute(f"ALTER TABLE contacts ADD COLUMN {column_name} {column_type}")
            logger.debug("Added %s to contacts table", column_name)
        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("contacts.%s already exists, skipping", column_name)
            else:
                raise

    await conn.commit()
