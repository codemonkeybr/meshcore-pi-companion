import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Create contact_telemetry_history table and tracked_telemetry_contacts setting."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}

    if "contact_telemetry_history" not in tables:
        await conn.execute(
            """
            CREATE TABLE contact_telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contact_telemetry_pk_ts
                ON contact_telemetry_history(public_key, timestamp)
            """
        )

    if "app_settings" in tables:
        col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "tracked_telemetry_contacts" not in columns:
            await conn.execute(
                "ALTER TABLE app_settings ADD COLUMN tracked_telemetry_contacts TEXT DEFAULT '[]'"
            )

    await conn.commit()
