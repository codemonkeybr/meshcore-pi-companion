"""Migration 063: Add virtual rooms and companion tables."""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS virtual_identities (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL,
            identity_type   TEXT NOT NULL CHECK(identity_type IN ('room','companion')),
            identity_key_hex TEXT NOT NULL,
            created_at      REAL NOT NULL DEFAULT (unixepoch())
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_virtual_identities_name_type
            ON virtual_identities(name, identity_type);

        CREATE TABLE IF NOT EXISTS room_messages (
            id              INTEGER PRIMARY KEY,
            room_name       TEXT NOT NULL,
            sender_key_hex  TEXT NOT NULL,
            text            TEXT NOT NULL,
            stored_at       REAL NOT NULL DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_room_messages_room
            ON room_messages(room_name, stored_at);

        CREATE TABLE IF NOT EXISTS room_acl (
            room_name       TEXT NOT NULL,
            client_key_hex  TEXT NOT NULL,
            auth_level      INTEGER NOT NULL DEFAULT 0,
            sync_since      INTEGER NOT NULL DEFAULT 0,
            push_failures   INTEGER NOT NULL DEFAULT 0,
            last_activity   REAL NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (room_name, client_key_hex)
        );

        CREATE TABLE IF NOT EXISTS companion_prefs (
            companion_name  TEXT PRIMARY KEY,
            prefs_json      TEXT NOT NULL,
            updated_at      REAL NOT NULL DEFAULT (unixepoch())
        );
    """)
