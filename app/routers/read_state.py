"""Read state management endpoints."""

import logging
import time

from fastapi import APIRouter

from app.models import UnreadCounts
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.services.radio_runtime import radio_runtime as radio_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/read-state", tags=["read-state"])


@router.get("/unreads", response_model=UnreadCounts)
async def get_unreads() -> UnreadCounts:
    """Get unread counts, mention flags, and last message times for all conversations.

    Computes unread counts server-side using last_read_at timestamps on
    channels and contacts, avoiding the need to fetch bulk messages.
    The radio's own name is sourced directly from the connected radio
    for @mention detection.
    """
    name: str | None = None
    mc = radio_manager.meshcore
    if mc and mc.self_info:
        name = mc.self_info.get("name") or None
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    data = await MessageRepository.get_unread_counts(
        name, blocked_keys=blocked_keys, blocked_names=blocked_names
    )
    return UnreadCounts(**data)


@router.post("/mark-all-read")
async def mark_all_read() -> dict:
    """Mark all contacts and channels as read.

    Updates last_read_at to current timestamp for all contacts and channels
    using two repository updates (same timestamp value across both tables).
    """
    now = int(time.time())

    await ContactRepository.mark_all_read(now)
    await ChannelRepository.mark_all_read(now)

    logger.info("Marked all contacts and channels as read at %d", now)
    return {"status": "ok", "timestamp": now}
