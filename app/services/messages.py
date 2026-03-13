import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.models import CONTACT_TYPE_REPEATER, Message, MessagePath
from app.repository import ContactRepository, MessageRepository, RawPacketRepository

if TYPE_CHECKING:
    from app.decoder import DecryptedDirectMessage

logger = logging.getLogger(__name__)

BroadcastFn = Callable[..., Any]
LOG_MESSAGE_PREVIEW_LEN = 32


def _truncate_for_log(text: str, max_chars: int = LOG_MESSAGE_PREVIEW_LEN) -> str:
    """Return a compact single-line message preview for log output."""
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."


def _format_channel_log_target(channel_name: str | None, channel_key: str) -> str:
    """Return a human-friendly channel label for logs."""
    return channel_name or channel_key


def _format_contact_log_target(contact_name: str | None, public_key: str) -> str:
    """Return a human-friendly DM target label for logs."""
    return contact_name or public_key[:12]


def build_message_paths(
    path: str | None,
    received_at: int,
    path_len: int | None = None,
) -> list[MessagePath] | None:
    """Build the single-path list used by message payloads."""
    return (
        [MessagePath(path=path or "", received_at=received_at, path_len=path_len)]
        if path is not None
        else None
    )


def build_message_model(
    *,
    message_id: int,
    msg_type: str,
    conversation_key: str,
    text: str,
    sender_timestamp: int | None,
    received_at: int,
    paths: list[MessagePath] | None = None,
    txt_type: int = 0,
    signature: str | None = None,
    sender_key: str | None = None,
    outgoing: bool = False,
    acked: int = 0,
    sender_name: str | None = None,
    channel_name: str | None = None,
) -> Message:
    """Build a Message model with the canonical backend payload shape."""
    return Message(
        id=message_id,
        type=msg_type,
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        paths=paths,
        txt_type=txt_type,
        signature=signature,
        sender_key=sender_key,
        outgoing=outgoing,
        acked=acked,
        sender_name=sender_name,
        channel_name=channel_name,
    )


def broadcast_message(
    *,
    message: Message,
    broadcast_fn: BroadcastFn,
    realtime: bool | None = None,
) -> None:
    """Broadcast a message payload, preserving the caller's broadcast signature."""
    payload = message.model_dump()
    if realtime is None:
        broadcast_fn("message", payload)
    else:
        broadcast_fn("message", payload, realtime=realtime)


def broadcast_message_acked(
    *,
    message_id: int,
    ack_count: int,
    paths: list[MessagePath] | None,
    broadcast_fn: BroadcastFn,
) -> None:
    """Broadcast a message_acked payload."""
    broadcast_fn(
        "message_acked",
        {
            "message_id": message_id,
            "ack_count": ack_count,
            "paths": [path.model_dump() for path in paths] if paths else [],
        },
    )


async def increment_ack_and_broadcast(
    *,
    message_id: int,
    broadcast_fn: BroadcastFn,
) -> int:
    """Increment a message's ACK count and broadcast the update."""
    ack_count = await MessageRepository.increment_ack_count(message_id)
    broadcast_fn("message_acked", {"message_id": message_id, "ack_count": ack_count})
    return ack_count


async def handle_duplicate_message(
    *,
    packet_id: int | None,
    msg_type: str,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
    path: str | None,
    received_at: int,
    path_len: int | None = None,
    broadcast_fn: BroadcastFn,
) -> None:
    """Handle a duplicate message by updating paths/acks on the existing record."""
    existing_msg = await MessageRepository.get_by_content(
        msg_type=msg_type,
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
    )
    if not existing_msg:
        label = "message" if msg_type == "CHAN" else "DM"
        logger.warning(
            "Duplicate %s for %s but couldn't find existing",
            label,
            conversation_key[:12],
        )
        return

    logger.debug(
        "Duplicate %s for %s (msg_id=%d, outgoing=%s) - adding path",
        msg_type,
        conversation_key[:12],
        existing_msg.id,
        existing_msg.outgoing,
    )

    if path is not None:
        paths = await MessageRepository.add_path(existing_msg.id, path, received_at, path_len)
    else:
        paths = existing_msg.paths or []

    if existing_msg.outgoing:
        ack_count = await MessageRepository.increment_ack_count(existing_msg.id)
    else:
        ack_count = existing_msg.acked

    if existing_msg.outgoing or path is not None:
        broadcast_message_acked(
            message_id=existing_msg.id,
            ack_count=ack_count,
            paths=paths,
            broadcast_fn=broadcast_fn,
        )

    if packet_id is not None:
        await RawPacketRepository.mark_decrypted(packet_id, existing_msg.id)


async def create_message_from_decrypted(
    *,
    packet_id: int,
    channel_key: str,
    sender: str | None,
    message_text: str,
    timestamp: int,
    received_at: int | None = None,
    path: str | None = None,
    path_len: int | None = None,
    channel_name: str | None = None,
    realtime: bool = True,
    broadcast_fn: BroadcastFn,
) -> int | None:
    """Store and broadcast a decrypted channel message."""
    received = received_at or int(time.time())
    text = f"{sender}: {message_text}" if sender else message_text
    channel_key_normalized = channel_key.upper()

    resolved_sender_key: str | None = None
    if sender:
        candidates = await ContactRepository.get_by_name(sender)
        if len(candidates) == 1:
            resolved_sender_key = candidates[0].public_key

    msg_id = await MessageRepository.create(
        msg_type="CHAN",
        text=text,
        conversation_key=channel_key_normalized,
        sender_timestamp=timestamp,
        received_at=received,
        path=path,
        path_len=path_len,
        sender_name=sender,
        sender_key=resolved_sender_key,
    )

    if msg_id is None:
        await handle_duplicate_message(
            packet_id=packet_id,
            msg_type="CHAN",
            conversation_key=channel_key_normalized,
            text=text,
            sender_timestamp=timestamp,
            path=path,
            received_at=received,
            path_len=path_len,
            broadcast_fn=broadcast_fn,
        )
        return None

    logger.info(
        'Stored channel message "%s" for %r (msg ID %d in chan ID %s)',
        _truncate_for_log(text),
        _format_channel_log_target(channel_name, channel_key_normalized),
        msg_id,
        channel_key_normalized,
    )
    await RawPacketRepository.mark_decrypted(packet_id, msg_id)

    broadcast_message(
        message=build_message_model(
            message_id=msg_id,
            msg_type="CHAN",
            conversation_key=channel_key_normalized,
            text=text,
            sender_timestamp=timestamp,
            received_at=received,
            paths=build_message_paths(path, received, path_len),
            sender_name=sender,
            sender_key=resolved_sender_key,
            channel_name=channel_name,
        ),
        broadcast_fn=broadcast_fn,
        realtime=realtime,
    )

    return msg_id


async def create_dm_message_from_decrypted(
    *,
    packet_id: int,
    decrypted: "DecryptedDirectMessage",
    their_public_key: str,
    our_public_key: str | None,
    received_at: int | None = None,
    path: str | None = None,
    path_len: int | None = None,
    outgoing: bool = False,
    realtime: bool = True,
    broadcast_fn: BroadcastFn,
) -> int | None:
    """Store and broadcast a decrypted direct message."""
    contact = await ContactRepository.get_by_key(their_public_key)
    if contact and contact.type == CONTACT_TYPE_REPEATER:
        logger.debug(
            "Skipping message from repeater %s (CLI responses not stored): %s",
            their_public_key[:12],
            (decrypted.message or "")[:50],
        )
        return None

    received = received_at or int(time.time())
    conversation_key = their_public_key.lower()
    sender_name = contact.name if contact and not outgoing else None

    msg_id = await MessageRepository.create(
        msg_type="PRIV",
        text=decrypted.message,
        conversation_key=conversation_key,
        sender_timestamp=decrypted.timestamp,
        received_at=received,
        path=path,
        path_len=path_len,
        outgoing=outgoing,
        sender_key=conversation_key if not outgoing else None,
        sender_name=sender_name,
    )

    if msg_id is None:
        await handle_duplicate_message(
            packet_id=packet_id,
            msg_type="PRIV",
            conversation_key=conversation_key,
            text=decrypted.message,
            sender_timestamp=decrypted.timestamp,
            path=path,
            received_at=received,
            path_len=path_len,
            broadcast_fn=broadcast_fn,
        )
        return None

    logger.info(
        'Stored direct message "%s" for %r (msg ID %d in contact ID %s, outgoing=%s)',
        _truncate_for_log(decrypted.message),
        _format_contact_log_target(contact.name if contact else None, conversation_key),
        msg_id,
        conversation_key,
        outgoing,
    )
    await RawPacketRepository.mark_decrypted(packet_id, msg_id)

    broadcast_message(
        message=build_message_model(
            message_id=msg_id,
            msg_type="PRIV",
            conversation_key=conversation_key,
            text=decrypted.message,
            sender_timestamp=decrypted.timestamp,
            received_at=received,
            paths=build_message_paths(path, received, path_len),
            outgoing=outgoing,
            sender_name=sender_name,
            sender_key=conversation_key if not outgoing else None,
        ),
        broadcast_fn=broadcast_fn,
        realtime=realtime,
    )

    await ContactRepository.update_last_contacted(conversation_key, received)
    return msg_id


async def create_fallback_direct_message(
    *,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
    received_at: int,
    path: str | None,
    path_len: int | None,
    txt_type: int,
    signature: str | None,
    sender_name: str | None,
    sender_key: str | None,
    broadcast_fn: BroadcastFn,
    message_repository=MessageRepository,
) -> Message | None:
    """Store and broadcast a CONTACT_MSG_RECV fallback direct message."""
    msg_id = await message_repository.create(
        msg_type="PRIV",
        text=text,
        conversation_key=conversation_key,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=path,
        path_len=path_len,
        txt_type=txt_type,
        signature=signature,
        sender_key=sender_key,
        sender_name=sender_name,
    )
    if msg_id is None:
        return None

    message = build_message_model(
        message_id=msg_id,
        msg_type="PRIV",
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        paths=build_message_paths(path, received_at, path_len),
        txt_type=txt_type,
        signature=signature,
        sender_key=sender_key,
        sender_name=sender_name,
    )
    broadcast_message(message=message, broadcast_fn=broadcast_fn)
    return message


async def create_fallback_channel_message(
    *,
    conversation_key: str,
    message_text: str,
    sender_timestamp: int,
    received_at: int,
    path: str | None,
    path_len: int | None,
    txt_type: int,
    sender_name: str | None,
    channel_name: str | None,
    broadcast_fn: BroadcastFn,
    message_repository=MessageRepository,
) -> Message | None:
    """Store and broadcast a CHANNEL_MSG_RECV fallback channel message."""
    conversation_key_normalized = conversation_key.upper()
    text = f"{sender_name}: {message_text}" if sender_name else message_text

    resolved_sender_key: str | None = None
    if sender_name:
        candidates = await ContactRepository.get_by_name(sender_name)
        if len(candidates) == 1:
            resolved_sender_key = candidates[0].public_key

    msg_id = await message_repository.create(
        msg_type="CHAN",
        text=text,
        conversation_key=conversation_key_normalized,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=path,
        path_len=path_len,
        txt_type=txt_type,
        sender_name=sender_name,
        sender_key=resolved_sender_key,
    )
    if msg_id is None:
        await handle_duplicate_message(
            packet_id=None,
            msg_type="CHAN",
            conversation_key=conversation_key_normalized,
            text=text,
            sender_timestamp=sender_timestamp,
            path=path,
            received_at=received_at,
            path_len=path_len,
            broadcast_fn=broadcast_fn,
        )
        return None

    message = build_message_model(
        message_id=msg_id,
        msg_type="CHAN",
        conversation_key=conversation_key_normalized,
        text=text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        paths=build_message_paths(path, received_at, path_len),
        txt_type=txt_type,
        sender_name=sender_name,
        sender_key=resolved_sender_key,
        channel_name=channel_name,
    )
    broadcast_message(message=message, broadcast_fn=broadcast_fn)
    return message


async def create_outgoing_direct_message(
    *,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
    received_at: int,
    broadcast_fn: BroadcastFn,
    message_repository=MessageRepository,
) -> Message | None:
    """Store and broadcast an outgoing direct message."""
    msg_id = await message_repository.create(
        msg_type="PRIV",
        text=text,
        conversation_key=conversation_key,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        outgoing=True,
    )
    if msg_id is None:
        return None

    message = build_message_model(
        message_id=msg_id,
        msg_type="PRIV",
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        outgoing=True,
        acked=0,
    )
    broadcast_message(message=message, broadcast_fn=broadcast_fn)
    return message


async def create_outgoing_channel_message(
    *,
    conversation_key: str,
    text: str,
    sender_timestamp: int,
    received_at: int,
    sender_name: str | None,
    sender_key: str | None,
    channel_name: str | None,
    broadcast_fn: BroadcastFn,
    message_repository=MessageRepository,
) -> Message | None:
    """Store and broadcast an outgoing channel message."""
    msg_id = await message_repository.create(
        msg_type="CHAN",
        text=text,
        conversation_key=conversation_key,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        outgoing=True,
        sender_name=sender_name,
        sender_key=sender_key,
    )
    if msg_id is None:
        return None

    message = build_message_model(
        message_id=msg_id,
        msg_type="CHAN",
        conversation_key=conversation_key,
        text=text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        outgoing=True,
        acked=0,
        sender_name=sender_name,
        sender_key=sender_key,
        channel_name=channel_name,
    )
    broadcast_message(message=message, broadcast_fn=broadcast_fn)
    return message
