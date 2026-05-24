import logging
import time
from collections.abc import AsyncIterator
from hashlib import sha256

from app.database import db
from app.decoder import PayloadType, extract_payload, get_packet_payload_type

logger = logging.getLogger(__name__)

UNDECRYPTED_PACKET_BATCH_SIZE = 500


class RawPacketRepository:
    @staticmethod
    async def create(data: bytes, timestamp: int | None = None) -> tuple[int, bool]:
        """
        Create a raw packet with payload-based deduplication.

        Returns (packet_id, is_new) tuple:
        - is_new=True: New packet stored, packet_id is the new row ID
        - is_new=False: Duplicate payload detected, packet_id is the existing row ID

        Deduplication is based on the SHA-256 hash of the packet payload
        (excluding routing/path information).
        """
        ts = timestamp if timestamp is not None else int(time.time())

        # Compute payload hash for deduplication
        payload = extract_payload(data)
        if payload:
            payload_hash = sha256(payload).digest()
        else:
            # For malformed packets, hash the full data
            payload_hash = sha256(data).digest()

        async with db.tx() as conn:
            async with conn.execute(
                "INSERT OR IGNORE INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
                (ts, data, payload_hash),
            ) as cursor:
                rowcount = cursor.rowcount
                lastrowid = cursor.lastrowid

            if rowcount > 0:
                assert lastrowid is not None
                return (lastrowid, True)

            # Duplicate payload — look up the existing row (same transaction).
            async with conn.execute(
                "SELECT id FROM raw_packets WHERE payload_hash = ?", (payload_hash,)
            ) as cursor:
                existing = await cursor.fetchone()
        assert existing is not None
        return (existing["id"], False)

    @staticmethod
    async def get_undecrypted_count() -> int:
        """Get count of undecrypted packets (those without a linked message)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as count FROM raw_packets WHERE message_id IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
        return row["count"] if row else 0

    @staticmethod
    async def get_oldest_undecrypted() -> int | None:
        """Get timestamp of oldest undecrypted packet, or None if none exist."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT MIN(timestamp) as oldest FROM raw_packets WHERE message_id IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
        return row["oldest"] if row and row["oldest"] is not None else None

    @staticmethod
    async def _stream_undecrypted_rows(
        batch_size: int,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Internal: keyset-paginated scan of every undecrypted raw packet.

        Yields ``(id, data, timestamp)`` for each row across all batches.
        Lock is acquired per batch only — concurrent writes can interleave
        at batch boundaries rather than being blocked for the full scan.
        Each batch opens a fresh cursor and consumes it fully with
        ``fetchall()`` before releasing, so no prepared statement is alive
        at a yield boundary.

        ``last_id`` advances per row, not per yield, so external filters
        (see ``stream_undecrypted_text_messages``) that drop rows do not
        cause a re-scan of skipped IDs.
        """
        last_id = -1
        while True:
            async with db.readonly() as conn:
                async with conn.execute(
                    "SELECT id, data, timestamp FROM raw_packets "
                    "WHERE message_id IS NULL AND id > ? ORDER BY id ASC LIMIT ?",
                    (last_id, batch_size),
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                return
            for row in rows:
                last_id = row["id"]
                yield (row["id"], bytes(row["data"]), row["timestamp"])

    @staticmethod
    async def stream_all_undecrypted(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Yield all undecrypted packets as (id, data, timestamp) in bounded batches."""
        async for row in RawPacketRepository._stream_undecrypted_rows(batch_size):
            yield row

    @staticmethod
    async def stream_undecrypted_text_messages(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Yield undecrypted TEXT_MESSAGE packets in bounded-size batches.

        Filters the shared scan to rows whose payload parses as a text
        message. Non-matching rows still advance the keyset cursor so they
        aren't re-fetched on subsequent batches.
        """
        async for packet_id, data, timestamp in RawPacketRepository._stream_undecrypted_rows(
            batch_size
        ):
            if get_packet_payload_type(data) == PayloadType.TEXT_MESSAGE:
                yield (packet_id, data, timestamp)

    @staticmethod
    async def count_undecrypted_text_messages(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> int:
        """Count undecrypted TEXT_MESSAGE packets without materializing them all."""
        count = 0
        async for _packet in RawPacketRepository.stream_undecrypted_text_messages(
            batch_size=batch_size
        ):
            count += 1
        return count

    @staticmethod
    async def mark_decrypted(packet_id: int, message_id: int) -> None:
        """Link a raw packet to its decrypted message."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE raw_packets SET message_id = ? WHERE id = ?",
                (message_id, packet_id),
            ):
                pass

    @staticmethod
    async def get_linked_message_id(packet_id: int) -> int | None:
        """Return the linked message ID for a raw packet, if any."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT message_id FROM raw_packets WHERE id = ?",
                (packet_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return row["message_id"]

    @staticmethod
    async def get_by_id(packet_id: int) -> tuple[int, bytes, int, int | None] | None:
        """Return a raw packet row as (id, data, timestamp, message_id)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, data, timestamp, message_id FROM raw_packets WHERE id = ?",
                (packet_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return (row["id"], bytes(row["data"]), row["timestamp"], row["message_id"])

    @staticmethod
    async def prune_old_undecrypted(max_age_days: int) -> int:
        """Delete undecrypted packets older than max_age_days. Returns count deleted."""
        cutoff = int(time.time()) - (max_age_days * 86400)
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM raw_packets WHERE message_id IS NULL AND timestamp < ?",
                (cutoff,),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount

    @staticmethod
    async def purge_linked_to_messages() -> int:
        """Delete raw packets that are already linked to a stored message."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM raw_packets WHERE message_id IS NOT NULL"
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount
