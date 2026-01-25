"""Tests for database migrations."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version


class TestMigrationSystem:
    """Test the migration version tracking system."""

    @pytest.mark.asyncio
    async def test_get_version_returns_zero_for_new_db(self):
        """New database has user_version=0."""
        conn = await aiosqlite.connect(":memory:")
        try:
            version = await get_version(conn)
            assert version == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_set_version_updates_pragma(self):
        """Setting version updates the user_version pragma."""
        conn = await aiosqlite.connect(":memory:")
        try:
            await set_version(conn, 5)
            version = await get_version(conn)
            assert version == 5
        finally:
            await conn.close()


class TestMigration001:
    """Test migration 001: add last_read_at columns."""

    @pytest.mark.asyncio
    async def test_migration_adds_last_read_at_to_contacts(self):
        """Migration adds last_read_at column to contacts table."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema without last_read_at (simulating pre-migration state)
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
                    last_contacted INTEGER
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations
            applied = await run_migrations(conn)

            assert applied == 10  # All 10 migrations run
            assert await get_version(conn) == 10

            # Verify columns exist by inserting and selecting
            await conn.execute(
                "INSERT INTO contacts (public_key, name, last_read_at) VALUES (?, ?, ?)",
                ("abc123", "Test", 12345),
            )
            await conn.execute(
                "INSERT INTO channels (key, name, last_read_at) VALUES (?, ?, ?)",
                ("KEY123", "#test", 67890),
            )
            await conn.commit()

            cursor = await conn.execute(
                "SELECT last_read_at FROM contacts WHERE public_key = ?", ("abc123",)
            )
            row = await cursor.fetchone()
            assert row["last_read_at"] == 12345

            cursor = await conn.execute(
                "SELECT last_read_at FROM channels WHERE key = ?", ("KEY123",)
            )
            row = await cursor.fetchone()
            assert row["last_read_at"] == 67890
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self):
        """Running migration multiple times is safe."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema without last_read_at
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations twice
            applied1 = await run_migrations(conn)
            applied2 = await run_migrations(conn)

            assert applied1 == 10  # All 10 migrations run
            assert applied2 == 0  # No migrations on second run
            assert await get_version(conn) == 10
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migration_handles_column_already_exists(self):
        """Migration handles case where column already exists."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema with last_read_at already present
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    last_read_at INTEGER
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_read_at INTEGER
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.commit()

            # Run migrations - should not fail
            applied = await run_migrations(conn)

            # All 10 migrations applied (version incremented) but no error
            assert applied == 10
            assert await get_version(conn) == 10
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_existing_data_preserved_after_migration(self):
        """Migration preserves existing contact and channel data."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            # Create schema and insert data before migration
            await conn.execute("""
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    type INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0
                )
            """)
            # Raw packets table with old schema (for migrations 2 and 3)
            await conn.execute("""
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    decrypted INTEGER DEFAULT 0,
                    message_id INTEGER,
                    decrypt_attempts INTEGER DEFAULT 0,
                    last_attempt INTEGER
                )
            """)
            await conn.execute("CREATE INDEX idx_raw_packets_decrypted ON raw_packets(decrypted)")
            # Messages table with old schema (for migrations 6 and 7)
            await conn.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    path_len INTEGER,
                    txt_type INTEGER DEFAULT 0,
                    signature TEXT,
                    outgoing INTEGER DEFAULT 0,
                    acked INTEGER DEFAULT 0,
                    UNIQUE(type, conversation_key, text, sender_timestamp)
                )
            """)
            await conn.execute(
                "INSERT INTO contacts (public_key, name, type) VALUES (?, ?, ?)",
                ("existingkey", "ExistingContact", 1),
            )
            await conn.execute(
                "INSERT INTO channels (key, name, is_hashtag) VALUES (?, ?, ?)",
                ("EXISTINGCHAN", "#existing", 1),
            )
            await conn.commit()

            # Run migrations
            await run_migrations(conn)

            # Verify data is preserved
            cursor = await conn.execute(
                "SELECT public_key, name, type, last_read_at FROM contacts WHERE public_key = ?",
                ("existingkey",),
            )
            row = await cursor.fetchone()
            assert row["public_key"] == "existingkey"
            assert row["name"] == "ExistingContact"
            assert row["type"] == 1
            assert row["last_read_at"] is None  # New column defaults to NULL

            cursor = await conn.execute(
                "SELECT key, name, is_hashtag, last_read_at FROM channels WHERE key = ?",
                ("EXISTINGCHAN",),
            )
            row = await cursor.fetchone()
            assert row["key"] == "EXISTINGCHAN"
            assert row["name"] == "#existing"
            assert row["is_hashtag"] == 1
            assert row["last_read_at"] is None
        finally:
            await conn.close()
