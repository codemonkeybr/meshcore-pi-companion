import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Add sender_name and sender_key columns to messages table.

    Backfill:
    - sender_name for CHAN messages: extract from "Name: message" format
    - sender_key for CHAN messages: match name to contact (skip ambiguous)
    - sender_key for incoming PRIV messages: set to conversation_key
    """
    # Guard: skip if messages table doesn't exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if not await cursor.fetchone():
        return

    for column in ["sender_name", "sender_key"]:
        try:
            await conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
            logger.debug("Added %s to messages table", column)
        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("messages.%s already exists, skipping", column)
            else:
                raise

    # Check which columns the messages table has (may be minimal in test environments)
    cursor = await conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in await cursor.fetchall()}

    # Only backfill if the required columns exist
    if "type" in msg_cols and "text" in msg_cols:
        # Count messages to backfill for progress reporting
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM messages WHERE type = 'CHAN' AND sender_name IS NULL"
        )
        row = await cursor.fetchone()
        chan_count = row[0] if row else 0
        if chan_count > 0:
            logger.info("Backfilling sender_name for %d channel messages...", chan_count)

        # Backfill sender_name for CHAN messages from "Name: message" format
        # Only extract if colon position is valid (> 1 and < 51, i.e. name is 1-50 chars)
        cursor = await conn.execute(
            """
            UPDATE messages SET sender_name = SUBSTR(text, 1, INSTR(text, ': ') - 1)
            WHERE type = 'CHAN' AND sender_name IS NULL
              AND INSTR(text, ': ') > 1 AND INSTR(text, ': ') < 52
            """
        )
        if cursor.rowcount > 0:
            logger.info("Backfilled sender_name for %d channel messages", cursor.rowcount)

        # Backfill sender_key for incoming PRIV messages
        if "outgoing" in msg_cols and "conversation_key" in msg_cols:
            cursor = await conn.execute(
                """
                UPDATE messages SET sender_key = conversation_key
                WHERE type = 'PRIV' AND outgoing = 0 AND sender_key IS NULL
                """
            )
            if cursor.rowcount > 0:
                logger.info("Backfilled sender_key for %d DM messages", cursor.rowcount)

        # Backfill sender_key for CHAN messages: match sender_name to contacts
        # Build name->key map, skip ambiguous names (multiple contacts with same name)
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
        )
        if await cursor.fetchone():
            cursor = await conn.execute(
                "SELECT public_key, name FROM contacts WHERE name IS NOT NULL AND name != ''"
            )
            rows = await cursor.fetchall()

            name_to_keys: dict[str, list[str]] = {}
            for row in rows:
                name = row["name"]
                key = row["public_key"]
                if name not in name_to_keys:
                    name_to_keys[name] = []
                name_to_keys[name].append(key)

            # Only use unambiguous names (single contact per name)
            unambiguous = {n: ks[0] for n, ks in name_to_keys.items() if len(ks) == 1}
            if unambiguous:
                logger.info(
                    "Matching sender_key for %d unique contact names...",
                    len(unambiguous),
                )
                # Use a temp table for a single bulk UPDATE instead of N individual queries
                await conn.execute(
                    "CREATE TEMP TABLE _name_key_map (name TEXT PRIMARY KEY, public_key TEXT NOT NULL)"
                )
                await conn.executemany(
                    "INSERT INTO _name_key_map (name, public_key) VALUES (?, ?)",
                    list(unambiguous.items()),
                )
                cursor = await conn.execute(
                    """
                    UPDATE messages SET sender_key = (
                        SELECT public_key FROM _name_key_map WHERE _name_key_map.name = messages.sender_name
                    )
                    WHERE type = 'CHAN' AND sender_key IS NULL
                      AND sender_name IN (SELECT name FROM _name_key_map)
                    """
                )
                updated = cursor.rowcount
                await conn.execute("DROP TABLE _name_key_map")
                if updated > 0:
                    logger.info("Backfilled sender_key for %d channel messages", updated)

    # Create index on sender_key for per-contact channel message counts
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender_key ON messages(sender_key)")

    await conn.commit()
    logger.debug("Added sender_name and sender_key columns with backfill")
