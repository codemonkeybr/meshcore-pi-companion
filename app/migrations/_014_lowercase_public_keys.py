import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Lowercase all contact public keys and related data for case-insensitive matching.

    Updates:
    - contacts.public_key (PRIMARY KEY) via temp table swap
    - messages.conversation_key for PRIV messages
    - app_settings.favorites (contact IDs)
    - app_settings.last_message_times (contact- prefixed keys)

    Handles case collisions by keeping the most-recently-seen contact.
    """

    # 1. Lowercase message conversation keys for private messages
    try:
        await conn.execute(
            "UPDATE messages SET conversation_key = lower(conversation_key) WHERE type = 'PRIV'"
        )
        logger.debug("Lowercased PRIV message conversation_keys")
    except aiosqlite.OperationalError as e:
        if "no such table" in str(e).lower():
            logger.debug("messages table does not exist yet, skipping conversation_key lowercase")
        else:
            raise

    # 2. Check if contacts table exists before proceeding
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
    )
    if not await cursor.fetchone():
        logger.debug("contacts table does not exist yet, skipping key lowercase")
        await conn.commit()
        return

    # 3. Handle contacts table - check for case collisions first
    cursor = await conn.execute(
        "SELECT lower(public_key) as lk, COUNT(*) as cnt "
        "FROM contacts GROUP BY lower(public_key) HAVING COUNT(*) > 1"
    )
    collisions = list(await cursor.fetchall())

    if collisions:
        logger.warning(
            "Found %d case-colliding contact groups, keeping most-recently-seen",
            len(collisions),
        )
        for row in collisions:
            lower_key = row[0]
            # Delete all but the most recently seen
            await conn.execute(
                """DELETE FROM contacts WHERE public_key IN (
                    SELECT public_key FROM contacts
                    WHERE lower(public_key) = ?
                    ORDER BY COALESCE(last_seen, 0) DESC
                    LIMIT -1 OFFSET 1
                )""",
                (lower_key,),
            )

    # 3. Rebuild contacts with lowercased keys
    # Get the actual column names from the table (handles different schema versions)
    cursor = await conn.execute("PRAGMA table_info(contacts)")
    columns_info = await cursor.fetchall()
    all_columns = [col[1] for col in columns_info]  # col[1] is column name

    # Build column lists, lowering public_key
    select_cols = ", ".join(f"lower({c})" if c == "public_key" else c for c in all_columns)
    col_defs = []
    for col in columns_info:
        name, col_type, _notnull, default, pk = col[1], col[2], col[3], col[4], col[5]
        parts = [name, col_type or "TEXT"]
        if pk:
            parts.append("PRIMARY KEY")
        if default is not None:
            parts.append(f"DEFAULT {default}")
        col_defs.append(" ".join(parts))

    create_sql = f"CREATE TABLE contacts_new ({', '.join(col_defs)})"
    await conn.execute(create_sql)
    await conn.execute(f"INSERT INTO contacts_new SELECT {select_cols} FROM contacts")
    await conn.execute("DROP TABLE contacts")
    await conn.execute("ALTER TABLE contacts_new RENAME TO contacts")

    # Recreate the on_radio index (if column exists)
    if "on_radio" in all_columns:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_on_radio ON contacts(on_radio)")

    # 4. Lowercase contact IDs in favorites JSON (if app_settings exists)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not await cursor.fetchone():
        await conn.commit()
        logger.info("Lowercased all contact public keys (no app_settings table)")
        return

    cursor = await conn.execute("SELECT favorites FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            favorites = json.loads(row[0])
            updated = False
            for fav in favorites:
                if fav.get("type") == "contact" and fav.get("id"):
                    new_id = fav["id"].lower()
                    if new_id != fav["id"]:
                        fav["id"] = new_id
                        updated = True
            if updated:
                await conn.execute(
                    "UPDATE app_settings SET favorites = ? WHERE id = 1",
                    (json.dumps(favorites),),
                )
                logger.debug("Lowercased contact IDs in favorites")
        except (json.JSONDecodeError, TypeError):
            pass

    # 5. Lowercase contact keys in last_message_times JSON
    cursor = await conn.execute("SELECT last_message_times FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row and row[0]:
        try:
            times = json.loads(row[0])
            new_times = {}
            updated = False
            for key, val in times.items():
                if key.startswith("contact-"):
                    new_key = "contact-" + key[8:].lower()
                    if new_key != key:
                        updated = True
                    new_times[new_key] = val
                else:
                    new_times[key] = val
            if updated:
                await conn.execute(
                    "UPDATE app_settings SET last_message_times = ? WHERE id = 1",
                    (json.dumps(new_times),),
                )
                logger.debug("Lowercased contact keys in last_message_times")
        except (json.JSONDecodeError, TypeError):
            pass

    await conn.commit()
    logger.info("Lowercased all contact public keys")
