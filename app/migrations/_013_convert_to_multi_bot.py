import json
import logging
import uuid

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Convert single bot_enabled/bot_code to multi-bot format.

    Adds a 'bots' TEXT column storing a JSON array of bot configs:
    [{"id": "uuid", "name": "Bot 1", "enabled": true, "code": "..."}]

    If existing bot_code is non-empty OR bot_enabled is true, migrates
    to a single bot named "Bot 1". Otherwise, creates empty array.

    Attempts to drop the old bot_enabled and bot_code columns.
    """

    # Add new bots column
    try:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN bots TEXT DEFAULT '[]'")
        logger.debug("Added bots column to app_settings")
    except aiosqlite.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.debug("bots column already exists, skipping")
        else:
            raise

    # Migrate existing bot data
    cursor = await conn.execute("SELECT bot_enabled, bot_code FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()

    if row:
        bot_enabled = bool(row[0]) if row[0] is not None else False
        bot_code = row[1] or ""

        # If there's existing bot data, migrate it
        if bot_code.strip() or bot_enabled:
            bots = [
                {
                    "id": str(uuid.uuid4()),
                    "name": "Bot 1",
                    "enabled": bot_enabled,
                    "code": bot_code,
                }
            ]
            bots_json = json.dumps(bots)
            logger.info("Migrating existing bot to multi-bot format: enabled=%s", bot_enabled)
        else:
            bots_json = "[]"

        await conn.execute(
            "UPDATE app_settings SET bots = ? WHERE id = 1",
            (bots_json,),
        )

    # Try to drop old columns (SQLite 3.35.0+ only)
    for column in ["bot_enabled", "bot_code"]:
        try:
            await conn.execute(f"ALTER TABLE app_settings DROP COLUMN {column}")
            logger.debug("Dropped %s column from app_settings", column)
        except aiosqlite.OperationalError as e:
            error_msg = str(e).lower()
            if "no such column" in error_msg:
                logger.debug("app_settings.%s already dropped, skipping", column)
            elif "syntax error" in error_msg or "drop column" in error_msg:
                # SQLite version doesn't support DROP COLUMN - harmless, column stays
                logger.debug("SQLite doesn't support DROP COLUMN, %s column will remain", column)
            else:
                raise

    await conn.commit()
