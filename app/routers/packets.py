import logging
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from app.database import db
from app.decoder import parse_packet, try_decrypt_packet_with_channel_key
from app.packet_processor import create_message_from_decrypted, run_historical_dm_decryption
from app.repository import ChannelRepository, RawPacketRepository
from app.websocket import broadcast_success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/packets", tags=["packets"])


class DecryptRequest(BaseModel):
    key_type: str = Field(description="Type of key: 'channel' or 'contact'")
    channel_key: str | None = Field(
        default=None, description="Channel key as hex (16 bytes = 32 chars)"
    )
    channel_name: str | None = Field(
        default=None, description="Channel name (for hashtag channels, key derived from name)"
    )
    # Fields for contact (DM) decryption
    private_key: str | None = Field(
        default=None,
        description="Our private key as hex (64 bytes = 128 chars, Ed25519 seed + pubkey)",
    )
    contact_public_key: str | None = Field(
        default=None, description="Contact's public key as hex (32 bytes = 64 chars)"
    )


class DecryptResult(BaseModel):
    started: bool
    total_packets: int
    message: str


async def _run_historical_channel_decryption(
    channel_key_bytes: bytes, channel_key_hex: str, display_name: str | None = None
) -> None:
    """Background task to decrypt historical packets with a channel key."""
    packets = await RawPacketRepository.get_all_undecrypted()
    total = len(packets)
    decrypted_count = 0

    if total == 0:
        logger.info("No undecrypted packets to process")
        return

    logger.info("Starting historical channel decryption of %d packets", total)

    for packet_id, packet_data, packet_timestamp in packets:
        result = try_decrypt_packet_with_channel_key(packet_data, channel_key_bytes)

        if result is not None:
            # Extract path from the raw packet for storage
            packet_info = parse_packet(packet_data)
            path_hex = packet_info.path.hex() if packet_info else None

            msg_id = await create_message_from_decrypted(
                packet_id=packet_id,
                channel_key=channel_key_hex,
                channel_name=display_name,
                sender=result.sender,
                message_text=result.message,
                timestamp=result.timestamp,
                received_at=packet_timestamp,
                path=path_hex,
                trigger_bot=False,  # Historical decryption should not trigger bot
            )

            if msg_id is not None:
                decrypted_count += 1

    logger.info(
        "Historical channel decryption complete: %d/%d packets decrypted", decrypted_count, total
    )

    # Notify frontend
    if decrypted_count > 0:
        name = display_name or channel_key_hex[:12]
        broadcast_success(
            f"Historical decrypt complete for {name}",
            f"Decrypted {decrypted_count} message{'s' if decrypted_count != 1 else ''}",
        )


@router.get("/undecrypted/count")
async def get_undecrypted_count() -> dict:
    """Get the count of undecrypted packets."""
    count = await RawPacketRepository.get_undecrypted_count()
    return {"count": count}


@router.post("/decrypt/historical", response_model=DecryptResult)
async def decrypt_historical_packets(
    request: DecryptRequest, background_tasks: BackgroundTasks
) -> DecryptResult:
    """
    Attempt to decrypt historical packets with the provided key.
    Runs in the background. Multiple decrypt jobs can run concurrently.
    """
    if request.key_type == "channel":
        # Channel decryption
        if request.channel_key:
            try:
                channel_key_bytes = bytes.fromhex(request.channel_key)
                if len(channel_key_bytes) != 16:
                    return DecryptResult(
                        started=False,
                        total_packets=0,
                        message="Channel key must be 16 bytes (32 hex chars)",
                    )
                channel_key_hex = request.channel_key.upper()
            except ValueError:
                return DecryptResult(
                    started=False,
                    total_packets=0,
                    message="Invalid hex string for channel key",
                )
        elif request.channel_name:
            channel_key_bytes = sha256(request.channel_name.encode("utf-8")).digest()[:16]
            channel_key_hex = channel_key_bytes.hex().upper()
        else:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="Must provide channel_key or channel_name",
            )

        # Get count and lookup channel name for display
        count = await RawPacketRepository.get_undecrypted_count()
        if count == 0:
            return DecryptResult(
                started=False, total_packets=0, message="No undecrypted packets to process"
            )

        # Try to find channel name for display
        channel = await ChannelRepository.get_by_key(channel_key_hex)
        display_name = channel.name if channel else request.channel_name

        background_tasks.add_task(
            _run_historical_channel_decryption, channel_key_bytes, channel_key_hex, display_name
        )

        return DecryptResult(
            started=True,
            total_packets=count,
            message=f"Started channel decryption of {count} packets in background",
        )

    elif request.key_type == "contact":
        # DM decryption
        if not request.private_key:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="Must provide private_key for contact decryption",
            )
        if not request.contact_public_key:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="Must provide contact_public_key for contact decryption",
            )

        try:
            private_key_bytes = bytes.fromhex(request.private_key)
            if len(private_key_bytes) != 64:
                return DecryptResult(
                    started=False,
                    total_packets=0,
                    message="Private key must be 64 bytes (128 hex chars)",
                )
        except ValueError:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="Invalid hex string for private key",
            )

        try:
            contact_public_key_bytes = bytes.fromhex(request.contact_public_key)
            if len(contact_public_key_bytes) != 32:
                return DecryptResult(
                    started=False,
                    total_packets=0,
                    message="Contact public key must be 32 bytes (64 hex chars)",
                )
            contact_public_key_hex = request.contact_public_key.lower()
        except ValueError:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="Invalid hex string for contact public key",
            )

        packets = await RawPacketRepository.get_undecrypted_text_messages()
        count = len(packets)
        if count == 0:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="No undecrypted TEXT_MESSAGE packets to process",
            )

        # Try to find contact name for display
        from app.repository import ContactRepository

        contact = await ContactRepository.get_by_key_or_prefix(contact_public_key_hex)
        display_name = contact.name if contact else None

        background_tasks.add_task(
            run_historical_dm_decryption,
            private_key_bytes,
            contact_public_key_bytes,
            contact_public_key_hex,
            display_name,
        )

        return DecryptResult(
            started=True,
            total_packets=count,
            message=f"Started DM decryption of {count} TEXT_MESSAGE packets in background",
        )

    return DecryptResult(
        started=False,
        total_packets=0,
        message="key_type must be 'channel' or 'contact'",
    )


class MaintenanceRequest(BaseModel):
    prune_undecrypted_days: int = Field(
        ge=1, description="Delete undecrypted packets older than this many days"
    )


class MaintenanceResult(BaseModel):
    packets_deleted: int
    vacuumed: bool


@router.post("/maintenance", response_model=MaintenanceResult)
async def run_maintenance(request: MaintenanceRequest) -> MaintenanceResult:
    """
    Clean up old undecrypted packets and reclaim disk space.

    - Deletes undecrypted packets older than the specified number of days
    - Runs VACUUM to reclaim disk space
    """
    logger.info(
        "Running maintenance: pruning packets older than %d days", request.prune_undecrypted_days
    )

    # Prune old undecrypted packets
    deleted = await RawPacketRepository.prune_old_undecrypted(request.prune_undecrypted_days)
    logger.info("Deleted %d old undecrypted packets", deleted)

    # Run VACUUM to reclaim space (must be outside transaction, use executescript)
    await db.conn.executescript("VACUUM;")
    logger.info("Database vacuumed")

    return MaintenanceResult(packets_deleted=deleted, vacuumed=True)
