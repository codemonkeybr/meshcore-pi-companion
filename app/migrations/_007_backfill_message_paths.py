import logging

import aiosqlite

logger = logging.getLogger(__name__)


def _extract_path_from_packet(raw_packet: bytes) -> str | None:
    """
    Extract path hex string from a raw packet using canonical framing validation.

    Returns the path as a hex string, or None if packet is malformed.
    """
    from app.path_utils import parse_packet_envelope

    envelope = parse_packet_envelope(raw_packet)
    return envelope.path.hex() if envelope is not None else None


async def migrate(conn: aiosqlite.Connection) -> None:
    """
    Backfill path column for messages that have linked raw_packets.

    For each message with a linked raw_packet (via message_id), extract the
    path from the raw packet and update the message.

    Only updates incoming messages (outgoing=0) since outgoing messages
    don't have meaningful path data.
    """
    # Get count of messages that need backfill
    cursor = await conn.execute(
        """
        SELECT COUNT(*)
        FROM messages m
        JOIN raw_packets rp ON rp.message_id = m.id
        WHERE m.path IS NULL AND m.outgoing = 0
        """
    )
    row = await cursor.fetchone()
    total = row[0] if row else 0

    if total == 0:
        logger.debug("No messages need path backfill")
        return

    logger.info("Backfilling path for %d messages. This may take a while...", total)

    # Process in batches
    batch_size = 1000
    processed = 0
    updated = 0

    cursor = await conn.execute(
        """
        SELECT m.id, rp.data
        FROM messages m
        JOIN raw_packets rp ON rp.message_id = m.id
        WHERE m.path IS NULL AND m.outgoing = 0
        ORDER BY m.id ASC
        """
    )

    updates: list[tuple[str, int]] = []  # (path, message_id)

    while True:
        rows = await cursor.fetchmany(batch_size)
        if not rows:
            break

        for row in rows:
            message_id = row[0]
            packet_data = bytes(row[1])

            path_hex = _extract_path_from_packet(packet_data)
            if path_hex is not None:
                updates.append((path_hex, message_id))

            processed += 1

        if processed % 10000 == 0:
            logger.info("Processed %d/%d messages...", processed, total)

    # Apply updates in batches
    if updates:
        logger.info("Updating %d messages with path data...", len(updates))
        for idx, (path_hex, message_id) in enumerate(updates, 1):
            await conn.execute(
                "UPDATE messages SET path = ? WHERE id = ?",
                (path_hex, message_id),
            )
            updated += 1
            if idx % 10000 == 0:
                logger.info("Updated %d/%d messages...", idx, len(updates))

    await conn.commit()
    logger.info("Path backfill complete: %d messages updated", updated)
