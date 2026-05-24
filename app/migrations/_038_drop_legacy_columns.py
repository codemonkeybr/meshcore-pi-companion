import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Drop legacy MQTT, community MQTT, and bots columns from app_settings.

    These columns were migrated to fanout_configs in migrations 36 and 37.
    SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN. For older versions,
    the columns remain but are harmless (no longer read or written).
    """
    # Check if app_settings table exists (some test DBs may not have it)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if await cursor.fetchone() is None:
        await conn.commit()
        return

    columns_to_drop = [
        "bots",
        "mqtt_broker_host",
        "mqtt_broker_port",
        "mqtt_username",
        "mqtt_password",
        "mqtt_use_tls",
        "mqtt_tls_insecure",
        "mqtt_topic_prefix",
        "mqtt_publish_messages",
        "mqtt_publish_raw_packets",
        "community_mqtt_enabled",
        "community_mqtt_iata",
        "community_mqtt_broker_host",
        "community_mqtt_broker_port",
        "community_mqtt_email",
    ]

    for column in columns_to_drop:
        try:
            await conn.execute(f"ALTER TABLE app_settings DROP COLUMN {column}")
            logger.debug("Dropped %s from app_settings", column)
        except aiosqlite.OperationalError as e:
            error_msg = str(e).lower()
            if "no such column" in error_msg:
                logger.debug("app_settings.%s already dropped, skipping", column)
            elif "syntax error" in error_msg or "drop column" in error_msg:
                logger.debug("SQLite doesn't support DROP COLUMN, %s column will remain", column)
            else:
                raise

    await conn.commit()
