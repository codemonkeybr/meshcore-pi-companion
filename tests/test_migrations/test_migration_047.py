"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration047:
    """Test migration 047: add statistics indexes."""

    @pytest.mark.asyncio
    async def test_adds_statistics_indexes(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 46)
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0,
                    last_seen INTEGER
                )
            """)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    received_at INTEGER NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash BLOB
                )
            """)
            await conn.commit()

            applied = await run_migrations(conn)

            assert applied == LATEST_SCHEMA_VERSION - 46
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            cursor = await conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                  AND name IN (
                      'idx_raw_packets_timestamp',
                      'idx_contacts_type_last_seen',
                      'idx_messages_type_received_conversation'
                  )
                ORDER BY name
                """
            )
            rows = await cursor.fetchall()
            assert [row["name"] for row in rows] == [
                "idx_contacts_type_last_seen",
                "idx_messages_type_received_conversation",
                "idx_raw_packets_timestamp",
            ]
        finally:
            await conn.close()
