"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration034:
    """Test migration 034: add flood_scope column to app_settings."""

    @pytest.mark.asyncio
    async def test_migration_adds_flood_scope_column(self):
        """Migration adds flood_scope column with empty string default."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 33)

            # Create app_settings without flood_scope (pre-migration schema)
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 200,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bots TEXT DEFAULT '[]',
                    mqtt_broker_host TEXT DEFAULT '',
                    mqtt_broker_port INTEGER DEFAULT 1883,
                    mqtt_username TEXT DEFAULT '',
                    mqtt_password TEXT DEFAULT '',
                    mqtt_use_tls INTEGER DEFAULT 0,
                    mqtt_tls_insecure INTEGER DEFAULT 0,
                    mqtt_topic_prefix TEXT DEFAULT 'meshcore',
                    mqtt_publish_messages INTEGER DEFAULT 0,
                    mqtt_publish_raw_packets INTEGER DEFAULT 0,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT 'mqtt-us-v1.letsmesh.net',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            # Channels table needed for migration 33
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            await conn.commit()

            await run_migrations(conn)

            # Verify column exists with correct default
            cursor = await conn.execute("SELECT flood_scope FROM app_settings WHERE id = 1")
            row = await cursor.fetchone()
            assert row["flood_scope"] == ""
        finally:
            await conn.close()
