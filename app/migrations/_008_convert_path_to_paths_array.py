import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Convert path TEXT column to paths TEXT column storing JSON array.

    The new format stores multiple paths as a JSON array of objects:
    [{"path": "1A2B", "received_at": 1234567890}, ...]

    This enables tracking multiple delivery paths for the same message
    (e.g., when a message is received via different repeater routes).
    """

    # First, add the new paths column
    try:
        await conn.execute("ALTER TABLE messages ADD COLUMN paths TEXT")
        logger.debug("Added paths column to messages table")
    except aiosqlite.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            logger.debug("messages.paths already exists, skipping column add")
        else:
            raise

    # Migrate existing path data to paths array format
    cursor = await conn.execute(
        "SELECT id, path, received_at FROM messages WHERE path IS NOT NULL AND paths IS NULL"
    )
    rows = list(await cursor.fetchall())

    if rows:
        logger.info("Converting %d messages from path to paths array format...", len(rows))
        for row in rows:
            message_id = row[0]
            old_path = row[1]
            received_at = row[2]

            # Convert single path to array format
            paths_json = json.dumps([{"path": old_path, "received_at": received_at}])
            await conn.execute(
                "UPDATE messages SET paths = ? WHERE id = ?",
                (paths_json, message_id),
            )

        logger.info("Converted %d messages to paths array format", len(rows))

    # Try to drop the old path column (SQLite 3.35.0+ only)
    try:
        await conn.execute("ALTER TABLE messages DROP COLUMN path")
        logger.debug("Dropped path column from messages table")
    except aiosqlite.OperationalError as e:
        error_msg = str(e).lower()
        if "no such column" in error_msg:
            logger.debug("messages.path already dropped, skipping")
        elif "syntax error" in error_msg or "drop column" in error_msg:
            # SQLite version doesn't support DROP COLUMN - harmless, column stays
            logger.debug("SQLite doesn't support DROP COLUMN, path column will remain")
        else:
            raise

    await conn.commit()
