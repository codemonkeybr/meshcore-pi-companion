"""Tests for database migration(s)."""

import json

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration013:
    """Test migration 013: convert bot_enabled/bot_code to multi-bot format."""

    @pytest.mark.asyncio
    async def test_migration_converts_existing_bot_to_array(self):
        """Migration converts existing bot_enabled/bot_code to bots array."""

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Set version to 12 (just before migration 13)
            await set_version(conn, 12)

            # Create app_settings with old bot columns
            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 50,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bot_enabled INTEGER DEFAULT 0,
                    bot_code TEXT DEFAULT ''
                )
            """)
            await conn.execute(
                "INSERT INTO app_settings (id, bot_enabled, bot_code) VALUES (1, 1, 'def bot(): return \"hello\"')"
            )
            await conn.commit()

            # Run migration 13 (plus remaining which also run)
            await run_migrations(conn)

            # Bots were migrated from app_settings to fanout_configs (migration 37)
            # and the bots column was dropped (migration 38)
            cursor = await conn.execute("SELECT * FROM fanout_configs WHERE type = 'bot'")
            row = await cursor.fetchone()
            assert row is not None

            config = json.loads(row["config"])
            assert config["code"] == 'def bot(): return "hello"'
            assert row["name"] == "Bot 1"
            assert bool(row["enabled"])
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_creates_empty_array_when_no_bot(self):
        """Migration creates empty bots array when no existing bot data."""

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 12)

            await conn.execute("""
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    max_radio_contacts INTEGER DEFAULT 50,
                    favorites TEXT DEFAULT '[]',
                    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
                    sidebar_sort_order TEXT DEFAULT 'recent',
                    last_message_times TEXT DEFAULT '{}',
                    preferences_migrated INTEGER DEFAULT 0,
                    advert_interval INTEGER DEFAULT 0,
                    last_advert_time INTEGER DEFAULT 0,
                    bot_enabled INTEGER DEFAULT 0,
                    bot_code TEXT DEFAULT ''
                )
            """)
            await conn.execute(
                "INSERT INTO app_settings (id, bot_enabled, bot_code) VALUES (1, 0, '')"
            )
            await conn.commit()

            await run_migrations(conn)

            # Bots column was dropped by migration 38; verify no bots in fanout_configs
            cursor = await conn.execute("SELECT COUNT(*) FROM fanout_configs WHERE type = 'bot'")
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()
