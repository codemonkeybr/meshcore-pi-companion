"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration033:
    """Test migration 033: seed #remoteterm channel."""

    @pytest.mark.asyncio
    async def test_migration_seeds_remoteterm_channel(self):
        """Migration inserts the #remoteterm channel for new installs."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 32)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            # Minimal app_settings so earlier migrations don't fail
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT '',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            await conn.commit()

            await run_migrations(conn)

            cursor = await conn.execute(
                "SELECT key, name, is_hashtag, on_radio FROM channels WHERE key = ?",
                ("8959AE053F2201801342A1DBDDA184F6",),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["name"] == "#remoteterm"
            assert row["is_hashtag"] == 1
            assert row["on_radio"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_does_not_overwrite_existing_channel(self):
        """Migration is a no-op if #remoteterm already exists."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 32)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    community_mqtt_enabled INTEGER DEFAULT 0,
                    community_mqtt_iata TEXT DEFAULT '',
                    community_mqtt_broker_host TEXT DEFAULT '',
                    community_mqtt_broker_port INTEGER DEFAULT 443,
                    community_mqtt_email TEXT DEFAULT ''
                )
            """)
            # Pre-existing channel with on_radio=1 (user added it to radio)
            await conn.execute(
                "INSERT INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
                ("8959AE053F2201801342A1DBDDA184F6", "#remoteterm", 1, 1),
            )
            await conn.commit()

            await run_migrations(conn)

            cursor = await conn.execute(
                "SELECT on_radio FROM channels WHERE key = ?",
                ("8959AE053F2201801342A1DBDDA184F6",),
            )
            row = await cursor.fetchone()
            assert row["on_radio"] == 1  # Not overwritten
        finally:
            await conn.close()
