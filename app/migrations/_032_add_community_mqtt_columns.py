import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add community MQTT configuration columns to app_settings."""
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
        ("community_mqtt_enabled", "INTEGER DEFAULT 0"),
        ("community_mqtt_iata", "TEXT DEFAULT ''"),
        ("community_mqtt_broker_host", "TEXT DEFAULT 'mqtt-us-v1.letsmesh.net'"),
        ("community_mqtt_broker_port", "INTEGER DEFAULT 443"),
        ("community_mqtt_email", "TEXT DEFAULT ''"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {col_name} {col_def}")

    await conn.commit()
