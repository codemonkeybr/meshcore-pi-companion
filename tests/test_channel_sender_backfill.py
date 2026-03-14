"""Tests for backfilling sender_key on channel messages when contacts become known."""

import pytest

from app.repository import ContactRepository, MessageRepository


@pytest.mark.asyncio
async def test_backfill_sets_sender_key_on_matching_messages(test_db):
    """Channel messages with a matching sender_name get sender_key backfilled."""
    pub_key = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
    channel_key = "AA" * 16

    # Store channel messages before the contact is known (sender_key=NULL)
    msg1 = await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: hello",
        conversation_key=channel_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
    )
    msg2 = await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: world",
        conversation_key=channel_key,
        sender_timestamp=200,
        received_at=200,
        sender_name="Alice",
    )
    assert msg1 is not None
    assert msg2 is not None

    # Verify sender_key is NULL before backfill
    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
    assert all(m.sender_key is None for m in messages)

    # Contact becomes known
    await ContactRepository.upsert(
        {
            "public_key": pub_key,
            "name": "Alice",
            "type": 1,
            "flags": 0,
        }
    )
    backfilled = await MessageRepository.backfill_channel_sender_key(pub_key, "Alice")
    assert backfilled == 2

    # Verify sender_key is now set
    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
    assert all(m.sender_key == pub_key.lower() for m in messages)


@pytest.mark.asyncio
async def test_backfill_skips_messages_with_existing_sender_key(test_db):
    """Messages that already have a sender_key are not overwritten."""
    pub_key_a = "aa" * 32
    pub_key_b = "bb" * 32
    channel_key = "CC" * 16

    # Message already attributed to pub_key_a
    msg = await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: hi",
        conversation_key=channel_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
        sender_key=pub_key_a,
    )
    assert msg is not None

    # A different contact also named "Alice" appears
    backfilled = await MessageRepository.backfill_channel_sender_key(pub_key_b, "Alice")
    assert backfilled == 0

    # Original attribution preserved
    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
    assert messages[0].sender_key == pub_key_a


@pytest.mark.asyncio
async def test_backfill_only_affects_matching_name(test_db):
    """Only messages from the matching sender_name are backfilled."""
    pub_key = "dd" * 32
    channel_key = "EE" * 16

    await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: hello",
        conversation_key=channel_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
    )
    await MessageRepository.create(
        msg_type="CHAN",
        text="Bob: hello",
        conversation_key=channel_key,
        sender_timestamp=101,
        received_at=101,
        sender_name="Bob",
    )

    await ContactRepository.upsert(
        {
            "public_key": pub_key,
            "name": "Alice",
            "type": 1,
            "flags": 0,
        }
    )
    backfilled = await MessageRepository.backfill_channel_sender_key(pub_key, "Alice")
    assert backfilled == 1

    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
    alice_msg = next(m for m in messages if m.sender_name == "Alice")
    bob_msg = next(m for m in messages if m.sender_name == "Bob")
    assert alice_msg.sender_key == pub_key.lower()
    assert bob_msg.sender_key is None


@pytest.mark.asyncio
async def test_backfill_does_not_touch_dms(test_db):
    """DM messages are never affected by channel sender backfill."""
    pub_key = "ff" * 32

    await MessageRepository.create(
        msg_type="PRIV",
        text="hello",
        conversation_key=pub_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
    )

    backfilled = await MessageRepository.backfill_channel_sender_key(pub_key, "Alice")
    assert backfilled == 0


@pytest.mark.asyncio
async def test_backfill_idempotent(test_db):
    """Running backfill twice has no effect the second time."""
    pub_key = "11" * 32
    channel_key = "22" * 16

    await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: test",
        conversation_key=channel_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
    )

    await ContactRepository.upsert(
        {
            "public_key": pub_key,
            "name": "Alice",
            "type": 1,
            "flags": 0,
        }
    )
    first = await MessageRepository.backfill_channel_sender_key(pub_key, "Alice")
    assert first == 1

    second = await MessageRepository.backfill_channel_sender_key(pub_key, "Alice")
    assert second == 0


@pytest.mark.asyncio
async def test_backfill_skips_ambiguous_duplicate_names(test_db):
    """Duplicate contact names should leave channel sender_key unresolved."""
    pub_key_a = "33" * 32
    pub_key_b = "44" * 32
    channel_key = "55" * 16

    await ContactRepository.upsert(
        {
            "public_key": pub_key_a,
            "name": "Alice",
            "type": 1,
            "flags": 0,
        }
    )
    await ContactRepository.upsert(
        {
            "public_key": pub_key_b,
            "name": "Alice",
            "type": 1,
            "flags": 0,
        }
    )
    await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: ambiguous",
        conversation_key=channel_key,
        sender_timestamp=100,
        received_at=100,
        sender_name="Alice",
    )

    backfilled = await MessageRepository.backfill_channel_sender_key(pub_key_a, "Alice")
    assert backfilled == 0

    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
    assert len(messages) == 1
    assert messages[0].sender_key is None
