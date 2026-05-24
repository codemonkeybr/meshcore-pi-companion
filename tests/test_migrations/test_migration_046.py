"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration046:
    """Test migration 046: clean orphaned contact child rows."""

    @pytest.mark.asyncio
    async def test_merges_uniquely_resolvable_orphans_and_drops_unresolved_ones(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 45)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE contact_name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    UNIQUE(public_key, name)
                )
            """)
            await conn.execute("""
                CREATE TABLE contact_advert_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_key TEXT NOT NULL,
                    path_hex TEXT NOT NULL,
                    path_len INTEGER NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    heard_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(public_key, path_hex, path_len)
                )
            """)

            resolved_prefix = "abc123"
            resolved_key = resolved_prefix + ("00" * 29)
            ambiguous_prefix = "deadbe"
            ambiguous_key_a = ambiguous_prefix + ("11" * 29)
            ambiguous_key_b = ambiguous_prefix + ("22" * 29)
            dead_prefix = "ffffaa"

            await conn.execute(
                "INSERT INTO contacts (public_key, name) VALUES (?, ?), (?, ?), (?, ?)",
                (
                    resolved_key,
                    "Resolved Sender",
                    ambiguous_key_a,
                    "Ambiguous A",
                    ambiguous_key_b,
                    "Ambiguous B",
                ),
            )
            await conn.execute(
                """
                INSERT INTO contact_name_history (public_key, name, first_seen, last_seen)
                VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)
                """,
                (
                    resolved_key,
                    "Resolved Sender",
                    900,
                    905,
                    resolved_prefix,
                    "Prefix Sender",
                    1000,
                    1010,
                    ambiguous_prefix,
                    "Ambiguous Prefix",
                    1100,
                    1110,
                ),
            )
            await conn.execute(
                """
                INSERT INTO contact_advert_paths
                    (public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                VALUES
                    (?, ?, ?, ?, ?, ?),
                    (?, ?, ?, ?, ?, ?),
                    (?, ?, ?, ?, ?, ?),
                    (?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_key,
                    "1122",
                    1,
                    950,
                    960,
                    2,
                    resolved_prefix,
                    "1122",
                    1,
                    1001,
                    1002,
                    3,
                    ambiguous_prefix,
                    "3344",
                    2,
                    1200,
                    1201,
                    1,
                    dead_prefix,
                    "5566",
                    1,
                    1300,
                    1301,
                    1,
                ),
            )
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 45
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            cursor = await conn.execute(
                """
                SELECT name, first_seen, last_seen
                FROM contact_name_history
                WHERE public_key = ?
                ORDER BY name
                """,
                (resolved_key,),
            )
            rows = await cursor.fetchall()
            assert [(row["name"], row["first_seen"], row["last_seen"]) for row in rows] == [
                ("Prefix Sender", 1000, 1010),
                ("Resolved Sender", 900, 905),
            ]

            cursor = await conn.execute(
                """
                SELECT path_hex, path_len, first_seen, last_seen, heard_count
                FROM contact_advert_paths
                WHERE public_key = ?
                ORDER BY path_hex, path_len
                """,
                (resolved_key,),
            )
            rows = await cursor.fetchall()
            assert [
                (
                    row["path_hex"],
                    row["path_len"],
                    row["first_seen"],
                    row["last_seen"],
                    row["heard_count"],
                )
                for row in rows
            ] == [
                ("1122", 1, 950, 1002, 5),
            ]

            for orphan_key in (resolved_prefix, ambiguous_prefix, dead_prefix):
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM contact_name_history WHERE public_key = ?",
                    (orphan_key,),
                )
                assert (await cursor.fetchone())[0] == 0
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM contact_advert_paths WHERE public_key = ?",
                    (orphan_key,),
                )
                assert (await cursor.fetchone())[0] == 0
        finally:
            await conn.close()
