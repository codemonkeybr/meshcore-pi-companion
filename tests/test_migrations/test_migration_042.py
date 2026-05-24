"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration042:
    """Test migration 042: add channels.flood_scope_override."""

    @pytest.mark.asyncio
    async def test_adds_channel_flood_scope_override_column(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 41)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 41
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            await conn.execute(
                """
                INSERT INTO channels (
                    key, name, is_hashtag, on_radio, flood_scope_override
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("AA" * 16, "#flightless", 1, 0, "#Esperance"),
            )
            await conn.commit()

            cursor = await conn.execute(
                "SELECT flood_scope_override FROM channels WHERE key = ?",
                ("AA" * 16,),
            )
            row = await cursor.fetchone()
            assert row["flood_scope_override"] == "#Esperance"
        finally:
            await conn.close()
