"""Tests for database migration(s)."""

from hashlib import sha256

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration028:
    """Test migration 028: convert payload_hash from TEXT to BLOB."""

    @pytest.mark.asyncio
    async def test_migration_converts_hex_text_to_blob(self):
        """Migration converts 64-char hex TEXT payload_hash values to 32-byte BLOBs."""

        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 27)

            # Create raw_packets with TEXT payload_hash (pre-migration schema)
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
            await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

            # Insert rows with hex TEXT hashes (as produced by .hexdigest())
            hash_a = sha256(b"packet_a").hexdigest()
            hash_b = sha256(b"packet_b").hexdigest()
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01\x02", hash_a),
            )
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?, ?, ?, ?)",
                (2000, b"\x03\x04", 42, hash_b),
            )
            # Row with NULL payload_hash
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data) VALUES (?, ?)",
                (3000, b"\x05\x06"),
            )
            await conn.commit()

            await run_migrations(conn)

            # Verify payload_hash column is now BLOB
            cursor = await conn.execute("PRAGMA table_info(raw_packets)")
            cols = {row[1]: row[2] for row in await cursor.fetchall()}
            assert cols["payload_hash"] == "BLOB"

            # Verify data is preserved and converted correctly
            cursor = await conn.execute(
                "SELECT id, timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 3

            assert rows[0]["timestamp"] == 1000
            assert bytes(rows[0]["data"]) == b"\x01\x02"
            assert bytes(rows[0]["payload_hash"]) == sha256(b"packet_a").digest()
            assert rows[0]["message_id"] is None

            assert rows[1]["timestamp"] == 2000
            assert bytes(rows[1]["payload_hash"]) == sha256(b"packet_b").digest()
            assert rows[1]["message_id"] == 42

            assert rows[2]["payload_hash"] is None

            # Verify unique index works
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_payload_hash'"
            )
            assert await cursor.fetchone() is not None

            # Verify message_id index exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE name='idx_raw_packets_message_id'"
            )
            assert await cursor.fetchone() is not None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_skips_when_already_blob(self):
        """Migration is a no-op when payload_hash is already BLOB (fresh install)."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 27)

            # Create raw_packets with BLOB payload_hash (new schema)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    message_id INTEGER,
                    payload_hash BLOB
                )
            """)
            await conn.execute(
                "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
            )

            # Insert a row with a BLOB hash
            await conn.execute(
                "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (1000, b"\x01", b"\xab" * 32),
            )
            await conn.commit()

            await run_migrations(conn)

            # Verify data unchanged
            cursor = await conn.execute("SELECT payload_hash FROM raw_packets")
            row = await cursor.fetchone()
            assert bytes(row["payload_hash"]) == b"\xab" * 32
        finally:
            await conn.close()
