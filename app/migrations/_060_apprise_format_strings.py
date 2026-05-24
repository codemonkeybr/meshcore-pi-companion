import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_BODY_FORMAT_DM = "**DM:** {sender_name}: {text} **via:** [{hops_backticked}]"
DEFAULT_BODY_FORMAT_CHANNEL = (
    "**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]"
)
_DEFAULT_BODY_FORMAT_DM_NO_PATH = "**DM:** {sender_name}: {text}"
_DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH = "**{channel_name}:** {sender_name}: {text}"


async def migrate(conn: aiosqlite.Connection) -> None:
    """Migrate apprise fanout configs from include_path boolean to format strings."""
    table_check = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fanout_configs'"
    )
    if not await table_check.fetchone():
        await conn.commit()
        return

    cursor = await conn.execute("SELECT id, config FROM fanout_configs WHERE type = 'apprise'")
    rows = await cursor.fetchall()

    for row in rows:
        config_id = row["id"] if isinstance(row, dict) else row[0]
        config_raw = row["config"] if isinstance(row, dict) else row[1]
        try:
            config = json.loads(config_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        # Skip if already migrated
        if "body_format_dm" in config:
            continue

        include_path = config.get("include_path", True)
        config["body_format_dm"] = (
            DEFAULT_BODY_FORMAT_DM if include_path else _DEFAULT_BODY_FORMAT_DM_NO_PATH
        )
        config["body_format_channel"] = (
            DEFAULT_BODY_FORMAT_CHANNEL if include_path else _DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH
        )
        config.pop("include_path", None)

        await conn.execute(
            "UPDATE fanout_configs SET config = ? WHERE id = ?",
            (json.dumps(config), config_id),
        )
        logger.info(
            "Migrated apprise config %s: include_path=%s -> format strings", config_id, include_path
        )

    await conn.commit()
