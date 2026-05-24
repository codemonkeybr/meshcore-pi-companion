import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Drop the UNIQUE(type, conversation_key, text, sender_timestamp) constraint on messages.

    This constraint creates a large autoindex (~13 MB on a 112K-row database) that
    stores the full message text in a B-tree. The idx_messages_dedup_null_safe unique
    index already provides identical dedup protection — no rows have NULL
    sender_timestamp since migration 15 backfilled them all.

    INSERT OR IGNORE still works correctly because it checks all unique constraints,
    including unique indexes like idx_messages_dedup_null_safe.

    Requires table recreation since SQLite doesn't support DROP CONSTRAINT.
    """
    # Check if the autoindex exists (indicates UNIQUE constraint)
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='sqlite_autoindex_messages_1'"
    )
    if not await cursor.fetchone():
        logger.debug("messages UNIQUE constraint already absent, skipping rebuild")
        await conn.commit()
        return

    logger.info("Rebuilding messages table to remove UNIQUE constraint...")

    # Get current columns from the existing table
    cursor = await conn.execute("PRAGMA table_info(messages)")
    old_cols = {col[1] for col in await cursor.fetchall()}

    # Target schema without the UNIQUE table constraint
    await conn.execute("""
        CREATE TABLE messages_new (
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

    # Copy only columns that exist in both old and new tables
    new_cols = {
        "id",
        "type",
        "conversation_key",
        "text",
        "sender_timestamp",
        "received_at",
        "txt_type",
        "signature",
        "outgoing",
        "acked",
        "paths",
    }
    copy_cols = ", ".join(sorted(c for c in new_cols if c in old_cols))

    await conn.execute(f"INSERT INTO messages_new ({copy_cols}) SELECT {copy_cols} FROM messages")
    await conn.execute("DROP TABLE messages")
    await conn.execute("ALTER TABLE messages_new RENAME TO messages")

    # Recreate indexes
    await conn.execute("CREATE INDEX idx_messages_conversation ON messages(type, conversation_key)")
    await conn.execute("CREATE INDEX idx_messages_received ON messages(received_at)")
    await conn.execute(
        """CREATE UNIQUE INDEX idx_messages_dedup_null_safe
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))"""
    )

    await conn.commit()
    logger.info("messages table rebuilt without UNIQUE constraint")
