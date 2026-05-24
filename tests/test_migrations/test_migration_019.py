"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration019:
    """Test migration 019: drop UNIQUE constraint from messages."""

    @pytest.mark.asyncio
    async def test_migration_drops_messages_unique_constraint(self):
        """Migration rebuilds messages without UNIQUE, preserving data and channel dedup index."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # raw_packets stub (no UNIQUE on data, so migration 18 skips)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            # Create messages WITH UNIQUE constraint — simulates production schema
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    paths TEXT,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.execute(
                "CREATE INDEX idx_messages_conversation ON messages(type, conversation_key)"
            )
            await conn.execute("CREATE INDEX idx_messages_received ON messages(received_at)")
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )

            # Insert test data
            await conn.execute(
                "INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, paths) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("CHAN", "KEY1", "hello world", 1000, 1000, '[{"path":"ab","received_at":1000}]'),
            )
            await conn.execute(
                "INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, outgoing) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("PRIV", "abc123", "dm text", 2000, 2000, 1),
            )
            await conn.commit()

            # Verify autoindex exists before migration
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_messages_1'"
            )
            assert await cursor.fetchone() is not None

            await run_migrations(conn)

            # Verify autoindex is gone
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_messages_1'"
            )
            assert await cursor.fetchone() is None

            # Verify data is preserved
            cursor = await conn.execute("SELECT COUNT(*) FROM messages")
            assert (await cursor.fetchone())[0] == 2

            cursor = await conn.execute(
                "SELECT type, conversation_key, text, paths, outgoing FROM messages ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows[0]["type"] == "CHAN"
            assert rows[0]["text"] == "hello world"
            assert rows[0]["paths"] == '[{"path":"ab","received_at":1000}]'
            assert rows[1]["type"] == "PRIV"
            assert rows[1]["outgoing"] == 1

            # Verify channel dedup index still works (INSERT OR IGNORE should ignore duplicates)
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("CHAN", "KEY1", "hello world", 1000, 9999),
            )
            assert cursor.rowcount == 0  # Duplicate ignored

            # Direct messages no longer use the shared dedup index.
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("PRIV", "abc123", "dm text", 2000, 9999),
            )
            assert cursor.rowcount == 1

            # Verify dedup index exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_messages_dedup_null_safe'"
            )
            assert await cursor.fetchone() is not None

            cursor = await conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='idx_messages_dedup_null_safe'"
            )
            index_sql = (await cursor.fetchone())["sql"]
            assert "WHERE type = 'CHAN'" in index_sql
        finally:
            await conn.close()
