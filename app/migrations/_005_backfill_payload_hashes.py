import logging
from hashlib import sha256

import aiosqlite

logger = logging.getLogger(__name__)


def _extract_payload_for_hash(raw_packet: bytes) -> bytes | None:
    """
    Extract payload from a raw packet for hashing using canonical framing validation.

    Returns the payload bytes, or None if packet is malformed.
    """
    from app.path_utils import parse_packet_envelope

    envelope = parse_packet_envelope(raw_packet)
    return envelope.payload if envelope is not None else None


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Backfill payload_hash for existing packets and remove duplicates.

    This may take a while for large databases. Progress is logged.
    After backfilling, a unique index is created to prevent future duplicates.
    """
    # Get count first
    cursor = await conn.execute("SELECT COUNT(*) FROM raw_packets WHERE payload_hash IS NULL")
    row = await cursor.fetchone()
    total = row[0] if row else 0

    if total == 0:
        logger.debug("No packets need hash backfill")
    else:
        logger.info("Backfilling payload hashes for %d packets. This may take a while...", total)

        # Process in batches to avoid memory issues
        batch_size = 1000
        processed = 0
        duplicates_deleted = 0

        # Track seen hashes to identify duplicates (keep oldest = lowest ID)
        seen_hashes: dict[str, int] = {}  # hash -> oldest packet ID

        # First pass: compute hashes and identify duplicates
        cursor = await conn.execute("SELECT id, data FROM raw_packets ORDER BY id ASC")

        packets_to_update: list[tuple[str, int]] = []  # (hash, id)
        ids_to_delete: list[int] = []

        while True:
            rows = await cursor.fetchmany(batch_size)
            if not rows:
                break

            for row in rows:
                packet_id = row[0]
                packet_data = bytes(row[1])

                # Extract payload and compute hash
                payload = _extract_payload_for_hash(packet_data)
                if payload:
                    payload_hash = sha256(payload).hexdigest()
                else:
                    # For malformed packets, hash the full data
                    payload_hash = sha256(packet_data).hexdigest()

                if payload_hash in seen_hashes:
                    # Duplicate - mark for deletion (we keep the older one)
                    ids_to_delete.append(packet_id)
                    duplicates_deleted += 1
                else:
                    # New hash - keep this packet
                    seen_hashes[payload_hash] = packet_id
                    packets_to_update.append((payload_hash, packet_id))

                processed += 1

            if processed % 10000 == 0:
                logger.info("Processed %d/%d packets...", processed, total)

        # Second pass: update hashes for packets we're keeping
        total_updates = len(packets_to_update)
        logger.info("Updating %d packets with hashes...", total_updates)
        for idx, (payload_hash, packet_id) in enumerate(packets_to_update, 1):
            await conn.execute(
                "UPDATE raw_packets SET payload_hash = ? WHERE id = ?",
                (payload_hash, packet_id),
            )
            if idx % 10000 == 0:
                logger.info("Updated %d/%d packets...", idx, total_updates)

        # Third pass: delete duplicates
        if ids_to_delete:
            total_deletes = len(ids_to_delete)
            logger.info("Removing %d duplicate packets...", total_deletes)
            deleted_count = 0
            # Delete in batches to avoid "too many SQL variables" error
            for i in range(0, len(ids_to_delete), 500):
                batch = ids_to_delete[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                await conn.execute(f"DELETE FROM raw_packets WHERE id IN ({placeholders})", batch)
                deleted_count += len(batch)
                if deleted_count % 10000 < 500:  # Log roughly every 10k
                    logger.info("Removed %d/%d duplicates...", deleted_count, total_deletes)

        await conn.commit()
        logger.info(
            "Hash backfill complete: %d packets updated, %d duplicates removed",
            len(packets_to_update),
            duplicates_deleted,
        )

    # Create unique index on payload_hash (this enforces uniqueness going forward)
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_packets_payload_hash "
            "ON raw_packets(payload_hash)"
        )
        logger.debug("Created unique index on payload_hash")
    except aiosqlite.OperationalError as e:
        if "already exists" not in str(e).lower():
            raise

    await conn.commit()
