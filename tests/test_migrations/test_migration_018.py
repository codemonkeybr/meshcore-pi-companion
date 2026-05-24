"""Tests for database migration(s)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration018:
    """Test migration 018: drop UNIQUE(data) from raw_packets."""

    @pytest.mark.asyncio
    async def test_migration_drops_data_unique_constraint(self):
        """Migration rebuilds raw_packets without UNIQUE(data), preserving data."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # Create raw_packets WITH UNIQUE(data) — simulates production schema
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL UNIQUE,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
            await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

            # Insert test data
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01\x02\x03", "hash_a"),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
                (2000, b"\x04\x05\x06", 42, "hash_b"),
            )
            # Create messages table stub (needed for migration 19)
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
                    paths TEXT
                )
            """)
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )
            await conn.commit()

            # Verify autoindex exists before migration
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_raw_packets_1'"
            )
            assert await cursor.fetchone() is not None

            await run_migrations(conn)

            # Verify autoindex is gone
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_autoindex_raw_packets_1'"
            )
            assert await cursor.fetchone() is None

            # Verify data is preserved
            cursor = await conn.execute("SELECT COUNT(*) FROM raw_packets")
            assert (await cursor.fetchone())[0] == 2

            cursor = await conn.execute(
                "SELECT timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows[0]["timestamp"] == 1000
            assert bytes(rows[0]["data"]) == b"\x01\x02\x03"
            assert rows[0]["message_id"] is None
            # payload_hash was converted from TEXT to BLOB by migration 28;
            # "hash_a" is not valid hex so gets sha256-hashed
            from hashlib import sha256

            assert bytes(rows[0]["payload_hash"]) == sha256(b"hash_a").digest()
            # message_id=42 was orphaned (no matching messages row), so
            # migration 49's orphan cleanup NULLs it out.
            assert rows[1]["message_id"] is None

            # Verify payload_hash unique index still works
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_payload_hash'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_skips_when_no_unique_constraint(self):
        """Migration is a no-op when UNIQUE(data) is already absent."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 17)

            # Create raw_packets WITHOUT UNIQUE(data) — fresh install schema
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash TEXT
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )
            # Messages stub for migration 19
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
                    paths TEXT
                )
            """)
            await conn.execute(
                """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
                   ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
            )
            await conn.commit()

            await run_migrations(conn)
        finally:
            await conn.close()
