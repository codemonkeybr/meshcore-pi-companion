import logging
from hashlib import sha256

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Convert payload_hash from 64-char hex TEXT to 32-byte BLOB.

    Halves storage for both the column data and its UNIQUE index.
    Uses Python bytes.fromhex() for the conversion since SQLite's unhex()
    requires 3.41.0+ which may not be available on all deployments.
    """
    # Guard: skip if raw_packets table doesn't exist
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_packets'"
    )
    if not await cursor.fetchone():
        logger.debug("raw_packets table does not exist, skipping payload_hash conversion")
        await conn.commit()
        return

    # Check column types — skip if payload_hash doesn't exist or is already BLOB
    cursor = await conn.execute("PRAGMA table_info(raw_packets)")
    cols = {row[1]: row[2] for row in await cursor.fetchall()}
    if "payload_hash" not in cols:
        logger.debug("payload_hash column does not exist, skipping conversion")
        await conn.commit()
        return
    if cols["payload_hash"].upper() == "BLOB":
        logger.debug("payload_hash is already BLOB, skipping conversion")
        await conn.commit()
        return

    logger.info("Rebuilding raw_packets to convert payload_hash TEXT → BLOB...")

    # Create new table with BLOB type
    await conn.execute("""
        CREATE TABLE raw_packets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            data BLOB NOT NULL,
            message_id INTEGER,
            payload_hash BLOB,
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )
    """)

    # Batch-convert rows: read TEXT hashes, convert to bytes, insert into new table
    batch_size = 5000
    cursor = await conn.execute(
        "SELECT id, timestamp, data, message_id, payload_hash FROM raw_packets ORDER BY id"
    )

    total = 0
    while True:
        rows = await cursor.fetchmany(batch_size)
        if not rows:
            break

        batch: list[tuple[int, int, bytes, int | None, bytes | None]] = []
        for row in rows:
            rid, ts, data, mid, ph = row[0], row[1], row[2], row[3], row[4]
            if ph is not None and isinstance(ph, str):
                try:
                    ph = bytes.fromhex(ph)
                except ValueError:
                    # Not a valid hex string — hash the value to produce a valid BLOB
                    ph = sha256(ph.encode()).digest()
            batch.append((rid, ts, data, mid, ph))

        await conn.executemany(
            "INSERT INTO raw_packets_new (id, timestamp, data, message_id, payload_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        total += len(batch)

        if total % 50000 == 0:
            logger.info("Converted %d rows...", total)

    # Preserve autoincrement sequence
    cursor = await conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'raw_packets'")
    seq_row = await cursor.fetchone()
    if seq_row is not None:
        await conn.execute(
            "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('raw_packets_new', ?)",
            (seq_row[0],),
        )

    await conn.execute("DROP TABLE raw_packets")
    await conn.execute("ALTER TABLE raw_packets_new RENAME TO raw_packets")

    # Clean up the sqlite_sequence entry for the old temp name
    await conn.execute("DELETE FROM sqlite_sequence WHERE name = 'raw_packets_new'")

    # Recreate indexes
    await conn.execute(
        "CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash)"
    )
    await conn.execute("CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id)")

    await conn.commit()
    logger.info("Converted %d payload_hash values from TEXT to BLOB", total)
