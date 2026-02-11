import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.event_handlers import track_pending_ack
from app.models import Message, SendChannelMessageRequest, SendDirectMessageRequest
from app.radio import radio_manager
from app.repository import MessageRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


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
) -> list[Message]:
    """List messages from the database."""
    return await MessageRepository.get_all(
        limit=limit,
        offset=offset,
        msg_type=type,
        conversation_key=conversation_key,
        before=before,
        before_id=before_id,
    )


@router.post("/direct", response_model=Message)
async def send_direct_message(request: SendDirectMessageRequest) -> Message:
    """Send a direct message to a contact."""
    mc = require_connected()

    # First check our database for the contact
    from app.repository import ContactRepository

    db_contact = await ContactRepository.get_by_key_or_prefix(request.destination)
    if not db_contact:
        raise HTTPException(
            status_code=404, detail=f"Contact not found in database: {request.destination}"
        )

    # Always add/update the contact on radio before sending.
    # The library cache (get_contact_by_key_prefix) can be stale after radio reboot,
    # so we can't rely on it to know if the firmware has the contact.
    # add_contact is idempotent - updates if exists, adds if not.
    contact_data = db_contact.to_radio_dict()
    async with radio_manager.radio_operation("send_direct_message"):
        logger.debug("Ensuring contact %s is on radio before sending", db_contact.public_key[:12])
        add_result = await mc.commands.add_contact(contact_data)
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

        result = await mc.commands.send_msg(
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

    # Trigger bots for outgoing DMs (runs in background, doesn't block response)
    from app.bot import run_bot_for_message

    asyncio.create_task(
        run_bot_for_message(
            sender_name=None,
            sender_key=db_contact.public_key.lower(),
            message_text=request.text,
            is_dm=True,
            channel_key=None,
            channel_name=None,
            sender_timestamp=now,
            path=None,
            is_outgoing=True,
        )
    )

    return message


# Temporary radio slot used for sending channel messages
TEMP_RADIO_SLOT = 0


@router.post("/channel", response_model=Message)
async def send_channel_message(request: SendChannelMessageRequest) -> Message:
    """Send a message to a channel."""
    mc = require_connected()

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

    async with radio_manager.radio_operation("send_channel_message"):
        # Load the channel to a temporary radio slot before sending
        set_result = await mc.commands.set_channel(
            channel_idx=TEMP_RADIO_SLOT,
            channel_name=db_channel.name,
            channel_secret=key_bytes,
        )
        if set_result.type == EventType.ERROR:
            logger.warning(
                "Failed to set channel on radio slot %d before sending: %s",
                TEMP_RADIO_SLOT,
                set_result.payload,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to configure channel on radio before sending message",
            )

        logger.info("Sending channel message to %s: %s", db_channel.name, request.text[:50])

        # Capture timestamp BEFORE sending so we can pass the same value to both the radio
        # and the database. This ensures the echo's timestamp matches our stored message
        # for proper deduplication.
        now = int(time.time())

        result = await mc.commands.send_chan_msg(
            chan=TEMP_RADIO_SLOT,
            msg=request.text,
            timestamp=now.to_bytes(4, "little"),  # Pass as bytes for compatibility
        )

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {result.payload}")

    # Store outgoing message with sender prefix (to match echo format)
    # The radio includes "SenderName: " prefix when broadcasting, so we store it the same way
    # to enable proper deduplication when the echo comes back
    channel_key_upper = request.channel_key.upper()
    radio_name = mc.self_info.get("name", "") if mc.self_info else ""
    text_with_sender = f"{radio_name}: {request.text}" if radio_name else request.text
    message_id = await MessageRepository.create(
        msg_type="CHAN",
        text=text_with_sender,
        conversation_key=channel_key_upper,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
    )
    if message_id is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to store outgoing message - unexpected duplicate",
        )

    message = Message(
        id=message_id,
        type="CHAN",
        conversation_key=channel_key_upper,
        text=text_with_sender,
        sender_timestamp=now,
        received_at=now,
        outgoing=True,
        acked=0,
    )

    # Trigger bots for outgoing channel messages (runs in background, doesn't block response)
    from app.bot import run_bot_for_message

    asyncio.create_task(
        run_bot_for_message(
            sender_name=radio_name or None,
            sender_key=None,
            message_text=request.text,
            is_dm=False,
            channel_key=channel_key_upper,
            channel_name=db_channel.name,
            sender_timestamp=now,
            path=None,
            is_outgoing=True,
        )
    )

    return message
