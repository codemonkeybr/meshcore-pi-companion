import json
import logging
import uuid

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Migrate bots from app_settings.bots JSON to fanout_configs rows."""

    try:
        cursor = await conn.execute("SELECT bots FROM app_settings WHERE id = 1")
        row = await cursor.fetchone()
    except Exception:
        row = None

    if row is None:
        await conn.commit()
        return

    bots_json = row["bots"] or "[]"
    try:
        bots = json.loads(bots_json)
    except (json.JSONDecodeError, TypeError):
        bots = []

    if not bots:
        await conn.commit()
        return

    import time

    now = int(time.time())

    # Use sort_order starting at 200 to place bots after MQTT configs (0-99)
    for i, bot in enumerate(bots):
        bot_name = bot.get("name") or f"Bot {i + 1}"
        bot_enabled = bool(bot.get("enabled", False))
        bot_code = bot.get("code", "")

        config_blob = json.dumps({"code": bot_code})
        scope = json.dumps({"messages": "all", "raw_packets": "none"})

        await conn.execute(
            """
            INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order, created_at)
            VALUES (?, 'bot', ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                bot_name,
                1 if bot_enabled else 0,
                config_blob,
                scope,
                200 + i,
                now,
            ),
        )
        logger.info("Migrated bot '%s' to fanout_configs (enabled=%s)", bot_name, bot_enabled)

    await conn.commit()
