"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration041:
    """Test migration 041: add nullable routing override columns."""

    @pytest.mark.asyncio
    async def test_adds_contact_routing_override_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 40)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    flags INTEGER DEFAULT 0,
                    last_path TEXT,
                    last_path_len INTEGER DEFAULT -1,
                    out_path_hash_mode INTEGER DEFAULT 0,
                    last_advert INTEGER,
                    lat REAL,
                    lon REAL,
                    last_seen INTEGER,
                    on_radio INTEGER DEFAULT 0,
                    last_contacted INTEGER,
                    first_seen INTEGER
                )
            """)
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 40
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            await conn.execute(
                """
                INSERT INTO contacts (
                    public_key,
                    route_override_path,
                    route_override_len,
                    route_override_hash_mode
                ) VALUES (?, ?, ?, ?)
                """,
                ("aa" * 32, "ae92f13e", 2, 1),
            )
            await conn.commit()

            cursor = await conn.execute(
                """
                SELECT route_override_path, route_override_len, route_override_hash_mode
                FROM contacts
                WHERE public_key = ?
                """,
                ("aa" * 32,),
            )
            row = await cursor.fetchone()
            assert row["route_override_path"] == "ae92f13e"
            assert row["route_override_len"] == 2
            assert row["route_override_hash_mode"] == 1
        finally:
            await conn.close()
