"""Shared contact/message reconciliation helpers."""

import logging

from app.repository import ContactNameHistoryRepository, ContactRepository, MessageRepository

logger = logging.getLogger(__name__)


async def promote_prefix_contacts_for_contact(
    *,
    public_key: str,
    contact_repository=ContactRepository,
    log: logging.Logger | None = None,
) -> list[str]:
    """Promote prefix-only placeholder contacts once a full key is known."""
    normalized_key = public_key.lower()
    promoted = await contact_repository.promote_prefix_placeholders(normalized_key)
    if promoted:
        (log or logger).info(
            "Promoted %d prefix contact placeholder(s) for %s",
            len(promoted),
            normalized_key[:12],
        )
    return promoted


async def claim_prefix_messages_for_contact(
    *,
    public_key: str,
    message_repository=MessageRepository,
    log: logging.Logger | None = None,
) -> int:
    """Promote prefix-key DMs to a resolved full public key."""
    normalized_key = public_key.lower()
    claimed = await message_repository.claim_prefix_messages(normalized_key)
    if claimed > 0:
        (log or logger).info(
            "Claimed %d prefix DM message(s) for contact %s",
            claimed,
            normalized_key[:12],
        )
    return claimed


async def backfill_channel_sender_for_contact(
    *,
    public_key: str,
    contact_name: str | None,
    message_repository=MessageRepository,
    log: logging.Logger | None = None,
) -> int:
    """Backfill channel sender attribution once a contact name is known."""
    if not contact_name:
        return 0

    normalized_key = public_key.lower()
    backfilled = await message_repository.backfill_channel_sender_key(
        normalized_key,
        contact_name,
    )
    if backfilled > 0:
        (log or logger).info(
            "Backfilled sender_key on %d channel message(s) for %s",
            backfilled,
            contact_name,
        )
    return backfilled


async def reconcile_contact_messages(
    *,
    public_key: str,
    contact_name: str | None,
    message_repository=MessageRepository,
    log: logging.Logger | None = None,
) -> tuple[int, int]:
    """Apply message reconciliation once a contact's identity is resolved."""
    claimed = await claim_prefix_messages_for_contact(
        public_key=public_key,
        message_repository=message_repository,
        log=log,
    )
    backfilled = await backfill_channel_sender_for_contact(
        public_key=public_key,
        contact_name=contact_name,
        message_repository=message_repository,
        log=log,
    )
    return claimed, backfilled


async def record_contact_name(
    *,
    public_key: str,
    contact_name: str | None,
    timestamp: int,
    contact_name_history_repository=ContactNameHistoryRepository,
) -> bool:
    """Record contact name history when a non-empty name is available."""
    if not contact_name:
        return False

    await contact_name_history_repository.record_name(
        public_key.lower(),
        contact_name,
        timestamp,
    )
    return True


async def record_contact_name_and_reconcile(
    *,
    public_key: str,
    contact_name: str | None,
    timestamp: int,
    message_repository=MessageRepository,
    contact_name_history_repository=ContactNameHistoryRepository,
    log: logging.Logger | None = None,
) -> tuple[int, int]:
    """Record name history, then reconcile message identity for the contact."""
    await record_contact_name(
        public_key=public_key,
        contact_name=contact_name,
        timestamp=timestamp,
        contact_name_history_repository=contact_name_history_repository,
    )
    return await reconcile_contact_messages(
        public_key=public_key,
        contact_name=contact_name,
        message_repository=message_repository,
        log=log,
    )
