"""Shared direct-message ACK application logic."""

from app.services import dm_ack_tracker
from app.services.messages import BroadcastFn, increment_ack_and_broadcast


async def apply_dm_ack_code(ack_code: str, *, broadcast_fn: BroadcastFn) -> bool:
    """Apply a DM ACK code using the shared pending/buffered state machine.

    Returns True when the ACK matched a pending message, False when it was buffered.
    """
    dm_ack_tracker.cleanup_expired_acks()

    message_id = dm_ack_tracker.pop_pending_ack(ack_code)
    if message_id is None:
        dm_ack_tracker.buffer_unmatched_ack(ack_code)
        return False

    dm_ack_tracker.clear_pending_acks_for_message(message_id)
    await increment_ack_and_broadcast(message_id=message_id, broadcast_fn=broadcast_fn)
    return True
