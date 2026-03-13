"""Shared pending ACK tracking for outgoing direct messages."""

import logging
import time

logger = logging.getLogger(__name__)

PendingAck = tuple[int, float, int]

_pending_acks: dict[str, PendingAck] = {}


def track_pending_ack(expected_ack: str, message_id: int, timeout_ms: int) -> None:
    """Track an expected ACK code for an outgoing direct message."""
    _pending_acks[expected_ack] = (message_id, time.time(), timeout_ms)
    logger.debug(
        "Tracking pending ACK %s for message %d (timeout %dms)",
        expected_ack,
        message_id,
        timeout_ms,
    )


def cleanup_expired_acks() -> None:
    """Remove stale pending ACK entries."""
    now = time.time()
    expired_codes = [
        code
        for code, (_message_id, created_at, timeout_ms) in _pending_acks.items()
        if now - created_at > (timeout_ms / 1000) * 2
    ]
    for code in expired_codes:
        del _pending_acks[code]
        logger.debug("Expired pending ACK %s", code)


def pop_pending_ack(ack_code: str) -> int | None:
    """Claim the tracked message ID for an ACK code if present."""
    pending = _pending_acks.pop(ack_code, None)
    if pending is None:
        return None
    message_id, _, _ = pending
    return message_id
