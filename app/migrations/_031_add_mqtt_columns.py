import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add MQTT configuration columns to app_settings."""
    # Guard: app_settings may not exist in partial-schema test setups
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
    )
    if not await cursor.fetchone():
        await conn.commit()
        return

    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}

    new_columns = [
        ("mqtt_broker_host", "TEXT DEFAULT ''"),
        ("mqtt_broker_port", "INTEGER DEFAULT 1883"),
        ("mqtt_username", "TEXT DEFAULT ''"),
        ("mqtt_password", "TEXT DEFAULT ''"),
        ("mqtt_use_tls", "INTEGER DEFAULT 0"),
        ("mqtt_tls_insecure", "INTEGER DEFAULT 0"),
        ("mqtt_topic_prefix", "TEXT DEFAULT 'meshcore'"),
        ("mqtt_publish_messages", "INTEGER DEFAULT 0"),
        ("mqtt_publish_raw_packets", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {col_name} {col_def}")

    await conn.commit()
