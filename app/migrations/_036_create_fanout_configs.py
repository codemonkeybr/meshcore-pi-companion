import json
import logging
import uuid

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Create fanout_configs table and migrate existing MQTT settings.

    Reads existing MQTT settings from app_settings and creates corresponding
    fanout_configs rows. Old columns are NOT dropped (rollback safety).
    """

    # 1. Create fanout_configs table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fanout_configs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER DEFAULT 0,
            config TEXT NOT NULL DEFAULT '{}',
            scope TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        )
        """
    )

    # 2. Read existing MQTT settings
    try:
        cursor = await conn.execute(
            """
            SELECT mqtt_broker_host, mqtt_broker_port, mqtt_username, mqtt_password,
                   mqtt_use_tls, mqtt_tls_insecure, mqtt_topic_prefix,
                   mqtt_publish_messages, mqtt_publish_raw_packets,
                   community_mqtt_enabled, community_mqtt_iata,
                   community_mqtt_broker_host, community_mqtt_broker_port,
                   community_mqtt_email
            FROM app_settings WHERE id = 1
            """
        )
        row = await cursor.fetchone()
    except Exception:
        row = None

    if row is None:
        await conn.commit()
        return

    import time

    now = int(time.time())
    sort_order = 0

    # 3. Migrate private MQTT if configured
    broker_host = row["mqtt_broker_host"] or ""
    if broker_host:
        publish_messages = bool(row["mqtt_publish_messages"])
        publish_raw = bool(row["mqtt_publish_raw_packets"])
        enabled = publish_messages or publish_raw

        config = {
            "broker_host": broker_host,
            "broker_port": row["mqtt_broker_port"] or 1883,
            "username": row["mqtt_username"] or "",
            "password": row["mqtt_password"] or "",
            "use_tls": bool(row["mqtt_use_tls"]),
            "tls_insecure": bool(row["mqtt_tls_insecure"]),
            "topic_prefix": row["mqtt_topic_prefix"] or "meshcore",
        }

        scope = {
            "messages": "all" if publish_messages else "none",
            "raw_packets": "all" if publish_raw else "none",
        }

        await conn.execute(
            """
            INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "mqtt_private",
                "Private MQTT",
                1 if enabled else 0,
                json.dumps(config),
                json.dumps(scope),
                sort_order,
                now,
            ),
        )
        sort_order += 1
        logger.info("Migrated private MQTT settings to fanout_configs (enabled=%s)", enabled)

    # 4. Migrate community MQTT if enabled OR configured (preserve disabled-but-configured)
    community_enabled = bool(row["community_mqtt_enabled"])
    community_iata = row["community_mqtt_iata"] or ""
    community_host = row["community_mqtt_broker_host"] or ""
    community_email = row["community_mqtt_email"] or ""
    community_has_config = bool(
        community_iata
        or community_email
        or (community_host and community_host != "mqtt-us-v1.letsmesh.net")
    )
    if community_enabled or community_has_config:
        config = {
            "broker_host": community_host or "mqtt-us-v1.letsmesh.net",
            "broker_port": row["community_mqtt_broker_port"] or 443,
            "iata": community_iata,
            "email": community_email,
        }

        scope = {
            "messages": "none",
            "raw_packets": "all",
        }

        await conn.execute(
            """
            INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                "mqtt_community",
                "Community MQTT",
                1 if community_enabled else 0,
                json.dumps(config),
                json.dumps(scope),
                sort_order,
                now,
            ),
        )
        logger.info(
            "Migrated community MQTT settings to fanout_configs (enabled=%s)", community_enabled
        )

    await conn.commit()
