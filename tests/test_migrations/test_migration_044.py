"""Tests for database migration(s)."""

import json

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration044:
    """Test migration 044: dedupe incoming direct messages."""

    @pytest.mark.asyncio
    async def test_migration_merges_incoming_dm_duplicates_and_adds_index(self):
        """Migration 44 collapses duplicate incoming DMs and re-links raw packets."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 43)

            await conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    paths TEXT,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    sender_name TEXT,
                    sender_key TEXT
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO messages
                    (id, type, conversation_key, text, sender_timestamp, received_at, paths,
                     txt_type, signature, outgoing, acked, sender_name, sender_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "PRIV", "abc123", "hello", 0, 1001, None, 0, None, 0, 0, None, "abc123"),
            )
            await conn.execute(
                """
                INSERT INTO messages
                    (id, type, conversation_key, text, sender_timestamp, received_at, paths,
                     txt_type, signature, outgoing, acked, sender_name, sender_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    2,
                    "PRIV",
                    "abc123",
                    "hello",
                    None,
                    1002,
                    json.dumps([{"path": "", "received_at": 1002, "path_len": 0}]),
                    2,
                    "abcd",
                    0,
                    0,
                    "Alice",
                    "abc123",
                ),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id) VALUES (?, ?, ?)",
                (1001, b"pkt1", 1),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id) VALUES (?, ?, ?)",
                (1002, b"pkt2", 2),
            )
            await conn.commit()

            await run_migrations(conn)

            cursor = await conn.execute("SELECT * FROM messages")
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["id"] == 1
            assert rows[0]["received_at"] == 1001
            assert rows[0]["signature"] == "abcd"
            assert rows[0]["txt_type"] == 2
            assert rows[0]["sender_name"] == "Alice"
            assert json.loads(rows[0]["paths"]) == [
                {"path": "", "received_at": 1002, "path_len": 0}
            ]

            cursor = await conn.execute("SELECT message_id FROM raw_packets ORDER BY id")
            assert [row["message_id"] for row in await cursor.fetchall()] == [1, 1]

            cursor = await conn.execute(
                "INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp, received_at, outgoing, sender_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("PRIV", "abc123", "hello", 0, 9999, 0, "abc123"),
            )
            assert cursor.rowcount == 0

            cursor = await conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='idx_messages_incoming_priv_dedup'"
            )
            index_sql = (await cursor.fetchone())["sql"]
            assert "WHERE type = 'PRIV' AND outgoing = 0" in index_sql
            assert "sender_key" in index_sql
        finally:
            await conn.close()
