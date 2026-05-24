import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Create repeater_telemetry_history table for JSON-blob telemetry snapshots."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repeater_telemetry_history (
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
        CREATE INDEX IF NOT EXISTS idx_repeater_telemetry_pk_ts
            ON repeater_telemetry_history (public_key, timestamp)
        """
    )
    await conn.commit()
