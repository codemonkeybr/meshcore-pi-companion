"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration020:
    """Test migration 020: enable WAL mode and incremental auto-vacuum."""

    @pytest.mark.asyncio
    async def test_migration_enables_wal_and_incremental_auto_vacuum(self, tmp_path):
        """Migration switches journal mode to WAL and auto_vacuum to INCREMENTAL."""
        db_path = str(tmp_path / "test.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 19)

            # Create minimal tables so migration 20 can run
            await conn.execute(
                "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY, data BLOB NOT NULL)"
            )
            await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            await conn.commit()

            # Verify defaults before migration
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 0  # NONE

            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "delete"

            await run_migrations(conn)

            # Verify WAL mode
            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "wal"

            # Verify incremental auto-vacuum
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 2  # INCREMENTAL
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, tmp_path):
        """Running migration 20 twice doesn't error or re-VACUUM."""
        db_path = str(tmp_path / "test.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            # Set up as if already at version 20 with WAL + incremental
            await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute(
                "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY, data BLOB NOT NULL)"
            )
            await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            await conn.commit()
            await set_version(conn, 20)

            await run_migrations(conn)

            # Still WAL + INCREMENTAL
            cursor = await conn.execute("PRAGMA journal_mode")
            assert (await cursor.fetchone())[0] == "wal"
            cursor = await conn.execute("PRAGMA auto_vacuum")
            assert (await cursor.fetchone())[0] == 2
        finally:
            await conn.close()
