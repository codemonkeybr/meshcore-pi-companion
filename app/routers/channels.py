import logging
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType
from pydantic import BaseModel, Field

from app.dependencies import require_connected
from app.models import Channel, ChannelDetail, ChannelMessageCounts, ChannelTopSender
from app.radio import radio_manager
from app.radio_sync import upsert_channel_from_radio_slot
from app.region_scope import normalize_region_scope
from app.repository import ChannelRepository, MessageRepository
from app.websocket import broadcast_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])


class CreateChannelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    key: str | None = Field(
        default=None,
        description="Channel key as hex string (32 chars = 16 bytes). If omitted or name starts with #, key is derived from name hash.",
    )


class ChannelFloodScopeOverrideRequest(BaseModel):
    flood_scope_override: str = Field(
        description="Blank clears the override; non-empty values temporarily override flood scope"
    )


@router.get("", response_model=list[Channel])
async def list_channels() -> list[Channel]:
    """List all channels from the database."""
    return await ChannelRepository.get_all()


@router.get("/{key}/detail", response_model=ChannelDetail)
async def get_channel_detail(key: str) -> ChannelDetail:
    """Get comprehensive channel profile data with message statistics."""
    channel = await ChannelRepository.get_by_key(key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    stats = await MessageRepository.get_channel_stats(channel.key)

    return ChannelDetail(
        channel=channel,
        message_counts=ChannelMessageCounts(**stats["message_counts"]),
        first_message_at=stats["first_message_at"],
        unique_sender_count=stats["unique_sender_count"],
        top_senders_24h=[ChannelTopSender(**s) for s in stats["top_senders_24h"]],
    )


@router.get("/{key}", response_model=Channel)
async def get_channel(key: str) -> Channel:
    """Get a specific channel by key (32-char hex string)."""
    channel = await ChannelRepository.get_by_key(key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@router.post("", response_model=Channel)
async def create_channel(request: CreateChannelRequest) -> Channel:
    """Create a channel in the database.

    Channels are NOT pushed to radio on creation. They are loaded to the radio
    automatically when sending a message (see messages.py send_channel_message).
    """
    is_hashtag = request.name.startswith("#")

    # Determine the channel secret
    if request.key and not is_hashtag:
        try:
            key_bytes = bytes.fromhex(request.key)
            if len(key_bytes) != 16:
                raise HTTPException(
                    status_code=400, detail="Channel key must be exactly 16 bytes (32 hex chars)"
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hex string for key") from None
    else:
        # Derive key from name hash (same as meshcore library does)
        key_bytes = sha256(request.name.encode("utf-8")).digest()[:16]

    key_hex = key_bytes.hex().upper()
    logger.info("Creating channel %s: %s (hashtag=%s)", key_hex, request.name, is_hashtag)

    # Store in database only - radio sync happens at send time
    await ChannelRepository.upsert(
        key=key_hex,
        name=request.name,
        is_hashtag=is_hashtag,
        on_radio=False,
    )

    return Channel(
        key=key_hex,
        name=request.name,
        is_hashtag=is_hashtag,
        on_radio=False,
        flood_scope_override=None,
    )


@router.post("/sync")
async def sync_channels_from_radio(max_channels: int = Query(default=40, ge=1, le=40)) -> dict:
    """Sync channels from the radio to the database."""
    require_connected()

    logger.info("Syncing channels from radio (checking %d slots)", max_channels)
    count = 0

    async with radio_manager.radio_operation("sync_channels_from_radio") as mc:
        for idx in range(max_channels):
            result = await mc.get_channel(idx)

            if result.type == EventType.CHANNEL_INFO:
                key_hex = await upsert_channel_from_radio_slot(result.payload, on_radio=True)
                if key_hex is not None:
                    count += 1
                    logger.debug(
                        "Synced channel %s: %s", key_hex, result.payload.get("channel_name")
                    )

    logger.info("Synced %d channels from radio", count)
    return {"synced": count}


@router.post("/{key}/mark-read")
async def mark_channel_read(key: str) -> dict:
    """Mark a channel as read (update last_read_at timestamp)."""
    channel = await ChannelRepository.get_by_key(key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    updated = await ChannelRepository.update_last_read_at(key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update read state")

    return {"status": "ok", "key": channel.key}


@router.post("/{key}/flood-scope-override", response_model=Channel)
async def set_channel_flood_scope_override(
    key: str, request: ChannelFloodScopeOverrideRequest
) -> Channel:
    """Set or clear a per-channel flood-scope override."""
    channel = await ChannelRepository.get_by_key(key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    override = normalize_region_scope(request.flood_scope_override) or None
    updated = await ChannelRepository.update_flood_scope_override(channel.key, override)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update flood-scope override")

    refreshed = await ChannelRepository.get_by_key(channel.key)
    if refreshed is None:
        raise HTTPException(status_code=500, detail="Channel disappeared after update")

    broadcast_event("channel", refreshed.model_dump())
    return refreshed


@router.delete("/{key}")
async def delete_channel(key: str) -> dict:
    """Delete a channel from the database by key.

    Note: This does not clear the channel from the radio. The radio's channel
    slots are managed separately (channels are loaded temporarily when sending).
    """
    logger.info("Deleting channel %s from database", key)
    await ChannelRepository.delete(key)

    broadcast_event("channel_deleted", {"key": key})

    return {"status": "ok"}
