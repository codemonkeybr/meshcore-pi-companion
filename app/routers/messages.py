import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.event_handlers import track_pending_ack
from app.models import (
    Message,
    MessagesAroundResponse,
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.radio import radio_manager
from app.region_scope import normalize_region_scope
from app.repository import AmbiguousPublicKeyPrefixError, AppSettingsRepository, MessageRepository
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


async def _send_channel_message_with_effective_scope(
    *,
    mc,
    channel,
    key_bytes: bytes,
    text: str,
    timestamp_bytes: bytes,
    action_label: str,
) -> Any:
    """Send a channel message, temporarily overriding flood scope when configured."""
    override_scope = normalize_region_scope(channel.flood_scope_override)
    baseline_scope = ""

    if override_scope:
        settings = await AppSettingsRepository.get()
        baseline_scope = normalize_region_scope(settings.flood_scope)

    if override_scope and override_scope != baseline_scope:
        logger.info(
            "Temporarily applying channel flood_scope override for %s: %r",
            channel.name,
            override_scope,
        )
        override_result = await mc.set_flood_scope(override_scope)
        if override_result is not None and override_result.type == EventType.ERROR:
            logger.warning(
                "Failed to apply channel flood_scope override for %s: %s",
                channel.name,
                override_result.payload,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Failed to apply regional override {override_scope!r} before {action_label}: "
                    f"{override_result.payload}"
                ),
            )

    try:
        set_result = await mc.set_channel(
            channel_idx=TEMP_RADIO_SLOT,
            channel_name=channel.name,
            channel_secret=key_bytes,
        )
        if set_result.type == EventType.ERROR:
            logger.warning(
                "Failed to set channel on radio slot %d before %s: %s",
                TEMP_RADIO_SLOT,
                action_label,
                set_result.payload,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to configure channel on radio before {action_label}",
            )

        return await mc.send_chan_msg(
            chan=TEMP_RADIO_SLOT,
            msg=text,
            timestamp=timestamp_bytes,
        )
    finally:
        if override_scope and override_scope != baseline_scope:
            try:
                restore_result = await mc.set_flood_scope(baseline_scope if baseline_scope else "")
                if restore_result is not None and restore_result.type == EventType.ERROR:
                    logger.error(
                        "Failed to restore baseline flood_scope after sending to %s: %s",
                        channel.name,
                        restore_result.payload,
                    )
                    broadcast_error(
                        "Regional override restore failed",
                        (
                            f"Sent to {channel.name}, but restoring flood scope failed. "
                            "The radio may still be region-scoped. Consider rebooting the radio."
                        ),
                    )
                else:
                    logger.debug(
                        "Restored baseline flood_scope after channel send: %r",
                        baseline_scope or "(disabled)",
                    )
            except Exception:
                logger.exception(
                    "Failed to restore baseline flood_scope after sending to %s",
                    channel.name,
                )
                broadcast_error(
                    "Regional override restore failed",
                    (
                        f"Sent to {channel.name}, but restoring flood scope failed. "
                        "The radio may still be region-scoped. Consider rebooting the radio."
                    ),
                )


@router.get("/around/{message_id}", response_model=MessagesAroundResponse)
async def get_messages_around(
    message_id: int,
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(default=None, description="Filter by conversation key"),
    context: int = Query(default=100, ge=1, le=500, description="Number of messages before/after"),
) -> MessagesAroundResponse:
    """Get messages around a specific message for jump-to-message navigation."""
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    messages, has_older, has_newer = await MessageRepository.get_around(
        message_id=message_id,
        msg_type=type,
        conversation_key=conversation_key,
        context_size=context,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
    )
    return MessagesAroundResponse(messages=messages, has_older=has_older, has_newer=has_newer)


@router.get("", response_model=list[Message])
async def list_messages(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(
        default=None, description="Filter by conversation key (channel key or contact pubkey)"
    ),
    before: int | None = Query(
        default=None, description="Cursor: received_at of last seen message"
    ),
    before_id: int | None = Query(default=None, description="Cursor: id of last seen message"),
    after: int | None = Query(
        default=None, description="Forward cursor: received_at of last seen message"
    ),
    after_id: int | None = Query(
        default=None, description="Forward cursor: id of last seen message"
    ),
    q: str | None = Query(default=None, description="Full-text search query"),
) -> list[Message]:
    """List messages from the database."""
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    return await MessageRepository.get_all(
        limit=limit,
        offset=offset,
        msg_type=type,
        conversation_key=conversation_key,
        before=before,
        before_id=before_id,
        after=after,
        after_id=after_id,
        q=q,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
    )


@router.post("/direct", response_model=Message)
async def send_direct_message(request: SendDirectMessageRequest) -> Message:
    """Send a direct message to a contact."""
    require_connected()

    # First check our database for the contact
    from app.repository import ContactRepository

    try:
        db_contact = await ContactRepository.get_by_key_or_prefix(request.destination)
    except AmbiguousPublicKeyPrefixError as err:
        sample = ", ".join(key[:12] for key in err.matches[:2])
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ambiguous destination key prefix '{err.prefix}'. "
                f"Use a full 64-character public key. Matching contacts: {sample}"
            ),
        ) from err
    if not db_contact:
        raise HTTPException(
            status_code=404, detail=f"Contact not found in database: {request.destination}"
        )

    # Always add/update the contact on radio before sending.
    # The library cache (get_contact_by_key_prefix) can be stale after radio reboot,
    # so we can't rely on it to know if the firmware has the contact.
    # add_contact is idempotent - updates if exists, adds if not.
    contact_data = db_contact.to_radio_dict()
    async with radio_manager.radio_operation("send_direct_message") as mc:
        logger.debug("Ensuring contact %s is on radio before sending", db_contact.public_key[:12])
        add_result = await mc.add_contact(contact_data)
        if add_result.type == EventType.ERROR:
            logger.warning("Failed to add contact to radio: %s", add_result.payload)
            # Continue anyway - might still work if contact exists

        # Get the contact from the library cache (may have updated info like path)
        contact = mc.get_contact_by_key_prefix(db_contact.public_key[:12])
        if not contact:
            contact = contact_data

        logger.info("Sending direct message to %s", db_contact.public_key[:12])

        # Capture timestamp BEFORE sending so we can pass the same value to both the radio
        # and the database. This ensures consistency for deduplication.
        now = int(time.time())

        result = await mc.send_msg(
            dst=contact,
            msg=request.text,
            timestamp=now,
        )

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {result.payload}")

    # Store outgoing message
    message_id = await MessageRepository.create(
        msg_type="PRIV",
        text=request.text,
        conversation_key=db_contact.public_key.lower(),
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
    )
    if message_id is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to store outgoing message - unexpected duplicate",
        )

    # Update last_contacted for the contact
    await ContactRepository.update_last_contacted(db_contact.public_key.lower(), now)

    # Track the expected ACK for this message
    expected_ack = result.payload.get("expected_ack")
    suggested_timeout: int = result.payload.get("suggested_timeout", 10000)  # default 10s
    if expected_ack:
        ack_code = expected_ack.hex() if isinstance(expected_ack, bytes) else expected_ack
        track_pending_ack(ack_code, message_id, suggested_timeout)
        logger.debug("Tracking ACK %s for message %d", ack_code, message_id)

    message = Message(
        id=message_id,
        type="PRIV",
        conversation_key=db_contact.public_key.lower(),
        text=request.text,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
        acked=0,
    )

    # Broadcast so all connected clients (not just sender) see the outgoing message immediately.
    # Fanout modules (including bots) are triggered via broadcast_event's realtime dispatch.
    broadcast_event("message", message.model_dump())

    return message


# Temporary radio slot used for sending channel messages
TEMP_RADIO_SLOT = 0


@router.post("/channel", response_model=Message)
async def send_channel_message(request: SendChannelMessageRequest) -> Message:
    """Send a message to a channel."""
    require_connected()

    # Get channel info from our database
    from app.decoder import calculate_channel_hash
    from app.repository import ChannelRepository

    db_channel = await ChannelRepository.get_by_key(request.channel_key)
    if not db_channel:
        raise HTTPException(
            status_code=404, detail=f"Channel {request.channel_key} not found in database"
        )

    # Convert channel key hex to bytes
    try:
        key_bytes = bytes.fromhex(request.channel_key)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid channel key format: {request.channel_key}"
        ) from None

    expected_hash = calculate_channel_hash(key_bytes)
    logger.info(
        "Sending to channel %s (%s) via radio slot %d, key hash: %s",
        request.channel_key,
        db_channel.name,
        TEMP_RADIO_SLOT,
        expected_hash,
    )
    channel_key_upper = request.channel_key.upper()
    message_id: int | None = None
    now: int | None = None
    radio_name: str = ""
    text_with_sender: str = request.text

    our_public_key: str | None = None

    async with radio_manager.radio_operation("send_channel_message") as mc:
        radio_name = mc.self_info.get("name", "") if mc.self_info else ""
        our_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
        text_with_sender = f"{radio_name}: {request.text}" if radio_name else request.text
        logger.info("Sending channel message to %s: %s", db_channel.name, request.text[:50])

        # Capture timestamp BEFORE sending so we can pass the same value to both the radio
        # and the database. This ensures the echo's timestamp matches our stored message
        # for proper deduplication.
        now = int(time.time())
        timestamp_bytes = now.to_bytes(4, "little")

        result = await _send_channel_message_with_effective_scope(
            mc=mc,
            channel=db_channel,
            key_bytes=key_bytes,
            text=request.text,
            timestamp_bytes=timestamp_bytes,
            action_label="sending message",
        )

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to send message: {result.payload}")

        # Store outgoing immediately after send to avoid a race where
        # our own echo lands before persistence.
        message_id = await MessageRepository.create(
            msg_type="CHAN",
            text=text_with_sender,
            conversation_key=channel_key_upper,
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
            sender_name=radio_name or None,
            sender_key=our_public_key,
        )
        if message_id is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to store outgoing message - unexpected duplicate",
            )

        # Broadcast immediately so all connected clients see the message promptly.
        # This ensures the message exists in frontend state when echo-driven
        # `message_acked` events arrive.
        broadcast_event(
            "message",
            Message(
                id=message_id,
                type="CHAN",
                conversation_key=channel_key_upper,
                text=text_with_sender,
                sender_timestamp=now,
                received_at=now,
                outgoing=True,
                acked=0,
                sender_name=radio_name or None,
                sender_key=our_public_key,
                channel_name=db_channel.name,
            ).model_dump(),
        )

    if message_id is None or now is None:
        raise HTTPException(status_code=500, detail="Failed to store outgoing message")

    acked_count, paths = await MessageRepository.get_ack_and_paths(message_id)

    message = Message(
        id=message_id,
        type="CHAN",
        conversation_key=channel_key_upper,
        text=text_with_sender,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
        acked=acked_count,
        paths=paths,
        sender_name=radio_name or None,
        sender_key=our_public_key,
        channel_name=db_channel.name,
    )

    return message


RESEND_WINDOW_SECONDS = 30


@router.post("/channel/{message_id}/resend")
async def resend_channel_message(
    message_id: int,
    new_timestamp: bool = Query(default=False),
) -> dict:
    """Resend a channel message.

    When new_timestamp=False (default): byte-perfect resend using the original timestamp.
    Only allowed within 30 seconds of the original send.

    When new_timestamp=True: resend with a fresh timestamp so repeaters treat it as a
    new packet. Creates a new message row in the database. No time window restriction.
    """
    require_connected()

    from app.repository import ChannelRepository

    msg = await MessageRepository.get_by_id(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if not msg.outgoing:
        raise HTTPException(status_code=400, detail="Can only resend outgoing messages")

    if msg.type != "CHAN":
        raise HTTPException(status_code=400, detail="Can only resend channel messages")

    if msg.sender_timestamp is None:
        raise HTTPException(status_code=400, detail="Message has no timestamp")

    # Byte-perfect resend enforces the 30s window; new-timestamp resend does not
    if not new_timestamp:
        elapsed = int(time.time()) - msg.sender_timestamp
        if elapsed > RESEND_WINDOW_SECONDS:
            raise HTTPException(status_code=400, detail="Resend window has expired (30 seconds)")

    db_channel = await ChannelRepository.get_by_key(msg.conversation_key)
    if not db_channel:
        raise HTTPException(status_code=404, detail=f"Channel {msg.conversation_key} not found")

    # Choose timestamp: original for byte-perfect, fresh for new-timestamp
    if new_timestamp:
        now = int(time.time())
        timestamp_bytes = now.to_bytes(4, "little")
    else:
        timestamp_bytes = msg.sender_timestamp.to_bytes(4, "little")

    try:
        key_bytes = bytes.fromhex(msg.conversation_key)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid channel key format: {msg.conversation_key}"
        ) from None

    resend_public_key: str | None = None

    async with radio_manager.radio_operation("resend_channel_message") as mc:
        # Strip sender prefix: DB stores "RadioName: message" but radio needs "message"
        radio_name = mc.self_info.get("name", "") if mc.self_info else ""
        resend_public_key = (mc.self_info.get("public_key") or None) if mc.self_info else None
        text_to_send = msg.text
        if radio_name and text_to_send.startswith(f"{radio_name}: "):
            text_to_send = text_to_send[len(f"{radio_name}: ") :]

        result = await _send_channel_message_with_effective_scope(
            mc=mc,
            channel=db_channel,
            key_bytes=key_bytes,
            text=text_to_send,
            timestamp_bytes=timestamp_bytes,
            action_label="resending message",
        )
        if result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500, detail=f"Failed to resend message: {result.payload}"
            )

    # For new-timestamp resend, create a new message row and broadcast it
    if new_timestamp:
        new_msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text=msg.text,
            conversation_key=msg.conversation_key,
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
            sender_name=radio_name or None,
            sender_key=resend_public_key,
        )
        if new_msg_id is None:
            # Timestamp-second collision (same text+channel within the same second).
            # The radio already transmitted, so log and return the original ID rather
            # than surfacing a 500 for a message that was successfully sent over the air.
            logger.warning(
                "Duplicate timestamp collision resending message %d — radio sent but DB row not created",
                message_id,
            )
            return {"status": "ok", "message_id": message_id}

        broadcast_event(
            "message",
            Message(
                id=new_msg_id,
                type="CHAN",
                conversation_key=msg.conversation_key,
                text=msg.text,
                sender_timestamp=now,
                received_at=now,
                outgoing=True,
                acked=0,
                sender_name=radio_name or None,
                sender_key=resend_public_key,
                channel_name=db_channel.name,
            ).model_dump(),
        )

        logger.info(
            "Resent channel message %d as new message %d to %s",
            message_id,
            new_msg_id,
            db_channel.name,
        )
        return {"status": "ok", "message_id": new_msg_id}

    logger.info("Resent channel message %d to %s", message_id, db_channel.name)
    return {"status": "ok", "message_id": message_id}
