"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration039:
    """Test migration 039: persist contacts.out_path_hash_mode."""

    @pytest.mark.asyncio
    async def test_legacy_advert_paths_do_not_become_direct_routes_after_upgrade(self):
        """Pre-045 advert-derived last_path data is dropped from active direct-route columns."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 38)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    flags INTEGER DEFAULT 0,
                    last_path TEXT,
                    last_path_len INTEGER DEFAULT -1,
                    last_advert INTEGER,
                    lat REAL,
                    lon REAL,
                    last_seen INTEGER,
                    on_radio INTEGER DEFAULT 0,
                    last_contacted INTEGER,
                    first_seen INTEGER
                )
            """)
            await conn.execute(
                """
                INSERT INTO contacts (
                    public_key, name, last_path, last_path_len, first_seen
                ) VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
                """,
                (
                    "aa" * 32,
                    "Flood",
                    "",
                    -1,
                    1000,
                    "bb" * 32,
                    "LegacyPath",
                    "1122",
                    1,
                    1001,
                ),
            )
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 38
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            cursor = await conn.execute(
                """
                SELECT public_key, direct_path, direct_path_len, direct_path_hash_mode
                FROM contacts
                ORDER BY public_key
                """
            )
            rows = await cursor.fetchall()
            assert rows[0]["public_key"] == "aa" * 32
            assert rows[0]["direct_path"] is None
            assert rows[0]["direct_path_len"] is None
            assert rows[0]["direct_path_hash_mode"] is None
            assert rows[1]["public_key"] == "bb" * 32
            assert rows[1]["direct_path"] is None
            assert rows[1]["direct_path_len"] is None
            assert rows[1]["direct_path_hash_mode"] is None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_legacy_out_path_hash_mode_is_not_promoted_into_direct_routes(self):
        """Pre-045 out_path_hash_mode does not make advert paths become active direct routes."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 38)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    flags INTEGER DEFAULT 0,
                    last_path TEXT,
                    last_path_len INTEGER DEFAULT -1,
                    out_path_hash_mode INTEGER NOT NULL DEFAULT 0,
                    last_advert INTEGER,
                    lat REAL,
                    lon REAL,
                    last_seen INTEGER,
                    on_radio INTEGER DEFAULT 0,
                    last_contacted INTEGER,
                    first_seen INTEGER
                )
            """)
            await conn.execute(
                """
                INSERT INTO contacts (
                    public_key, name, last_path, last_path_len, out_path_hash_mode, first_seen
                ) VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)
                """,
                (
                    "cc" * 32,
                    "Multi",
                    "aa00bb00",
                    2,
                    1,
                    1000,
                    "dd" * 32,
                    "Flood",
                    "",
                    -1,
                    0,
                    1001,
                ),
            )
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 38
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            cursor = await conn.execute(
                """
                SELECT public_key, direct_path, direct_path_len, direct_path_hash_mode
                FROM contacts
                WHERE public_key IN (?, ?)
                ORDER BY public_key
                """,
                ("cc" * 32, "dd" * 32),
            )
            rows = await cursor.fetchall()
            assert rows[0]["public_key"] == "cc" * 32
            assert rows[0]["direct_path"] is None
            assert rows[0]["direct_path_len"] is None
            assert rows[0]["direct_path_hash_mode"] is None
            assert rows[1]["public_key"] == "dd" * 32
            assert rows[1]["direct_path"] is None
            assert rows[1]["direct_path_len"] is None
            assert rows[1]["direct_path_hash_mode"] is None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_existing_direct_route_columns_are_preserved(self):
        """Already-migrated databases keep canonical direct-route data intact."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 44)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    flags INTEGER DEFAULT 0,
                    direct_path TEXT,
                    direct_path_len INTEGER,
                    direct_path_hash_mode INTEGER,
                    direct_path_updated_at INTEGER,
                    route_override_path TEXT,
                    route_override_len INTEGER,
                    route_override_hash_mode INTEGER,
                    last_advert INTEGER,
                    lat REAL,
                    lon REAL,
                    last_seen INTEGER,
                    on_radio INTEGER DEFAULT 0,
                    last_contacted INTEGER,
                    first_seen INTEGER,
                    last_read_at INTEGER
                )
            """)
            await conn.execute(
                """
                INSERT INTO contacts (
                    public_key, name, direct_path, direct_path_len, direct_path_hash_mode,
                    direct_path_updated_at, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("ee" * 32, "Direct", "aa00bb00", 2, 1, 123456, 123457),
            )
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 44
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            cursor = await conn.execute(
                """
                SELECT direct_path, direct_path_len, direct_path_hash_mode, direct_path_updated_at
                FROM contacts
                WHERE public_key = ?
                """,
                ("ee" * 32,),
            )
            row = await cursor.fetchone()
            assert row["direct_path"] == "aa00bb00"
            assert row["direct_path_len"] == 2
            assert row["direct_path_hash_mode"] == 1
            assert row["direct_path_updated_at"] == 123456
        finally:
            await conn.close()
