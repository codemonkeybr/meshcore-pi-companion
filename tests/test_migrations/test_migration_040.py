"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration040:
    """Test migration 040: include path_len in advert-path identity."""

    @pytest.mark.asyncio
    async def test_rebuilds_contact_advert_paths_to_distinguish_same_bytes_by_hop_count(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 39)
            await conn.execute("""
                CREATE TABLE contact_advert_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_key TEXT NOT NULL,
                    path_hex TEXT NOT NULL,
                    path_len INTEGER NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    heard_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(public_key, path_hex)
                )
            """)
            await conn.execute(
                """
                INSERT INTO contact_advert_paths
                    (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("aa" * 32, "aa00", 1, 1000, 1001, 2),
            )
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 39
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            await conn.execute(
                """
                INSERT INTO contact_advert_paths
                    (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("aa" * 32, "aa00", 2, 1002, 1002, 1),
            )
            await conn.commit()

            cursor = await conn.execute(
                """
                SELECT path_hex, path_len, heard_count
                FROM contact_advert_paths
                WHERE public_key = ?
                ORDER BY path_len ASC
                """,
                ("aa" * 32,),
            )
            rows = await cursor.fetchall()
            assert [(row["path_hex"], row["path_len"], row["heard_count"]) for row in rows] == [
                ("aa00", 1, 2),
                ("aa00", 2, 1),
            ]
        finally:
            await conn.close()
