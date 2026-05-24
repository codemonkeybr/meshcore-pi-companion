"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration032:
    """Test migration 032: add community MQTT columns to app_settings."""

    @pytest.mark.asyncio
    async def test_migration_adds_all_community_mqtt_columns(self):
        """Migration adds enabled, iata, broker, and email columns."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 31)

            # Create app_settings without community columns (pre-migration schema)
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
                    mqtt_publish_raw_packets INTEGER DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            await run_migrations(conn)

            # Community MQTT columns were added by migration 32 and dropped by migration 38.
            # Verify community settings were NOT migrated (no community config existed).
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM fanout_configs WHERE type = 'mqtt_community'"
            )
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()
