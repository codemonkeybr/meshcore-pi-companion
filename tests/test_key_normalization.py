"""Tests for public key case normalization."""

import pytest

from app.repository import AmbiguousPublicKeyPrefixError, ContactRepository, MessageRepository


@pytest.mark.asyncio
async def test_upsert_stores_lowercase_key(test_db):
    await ContactRepository.upsert(
        {"public_key": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"}
    )
    contact = await ContactRepository.get_by_key(
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    )
    assert contact is not None
    assert contact.public_key == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


@pytest.mark.asyncio
async def test_get_by_key_case_insensitive(test_db):
    await ContactRepository.upsert(
        {"public_key": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}
    )
    contact = await ContactRepository.get_by_key(
        "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"
    )
    assert contact is not None


@pytest.mark.asyncio
async def test_update_last_contacted_case_insensitive(test_db):
    key = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    await ContactRepository.upsert({"public_key": key})

    await ContactRepository.update_last_contacted(key.upper(), 12345)
    contact = await ContactRepository.get_by_key(key)
    assert contact is not None
    assert contact.last_contacted == 12345


@pytest.mark.asyncio
async def test_get_by_pubkey_first_byte(test_db):
    key1 = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    key2 = "a1ffddeeaabb1122334455667788990011223344556677889900aabbccddeeff00"
    key3 = "b2b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    for key in [key1, key2, key3]:
        await ContactRepository.upsert({"public_key": key})

    results = await ContactRepository.get_by_pubkey_first_byte("a1")
    assert len(results) == 2
    result_keys = {c.public_key for c in results}
    assert key1 in result_keys
    assert key2 in result_keys

    results = await ContactRepository.get_by_pubkey_first_byte("A1")
    assert len(results) == 2  # case insensitive


@pytest.mark.asyncio
async def test_null_sender_timestamp_defaults_to_received_at(test_db):
    """Verify that a None/0 sender_timestamp is replaced by received_at."""
    msg_id = await MessageRepository.create(
        msg_type="PRIV",
        text="hello",
        conversation_key="abcd1234" * 8,
        sender_timestamp=500,  # simulates fallback: `payload.get("sender_timestamp") or received_at`
        received_at=500,
    )
    assert msg_id is not None

    messages = await MessageRepository.get_all(
        msg_type="PRIV", conversation_key="abcd1234" * 8, limit=10
    )
    assert len(messages) == 1
    assert messages[0].sender_timestamp == 500


@pytest.mark.asyncio
async def test_incoming_direct_messages_with_same_text_and_timestamp_dedup(test_db):
    """Incoming direct messages now collapse onto one content-identity row."""
    received_at = 600
    msg_id1 = await MessageRepository.create(
        msg_type="PRIV",
        text="hello",
        conversation_key="abcd1234" * 8,
        sender_timestamp=received_at,
        received_at=received_at,
    )
    assert msg_id1 is not None

    msg_id2 = await MessageRepository.create(
        msg_type="PRIV",
        text="hello",
        conversation_key="abcd1234" * 8,
        sender_timestamp=received_at,
        received_at=received_at,
    )
    assert msg_id2 is None


@pytest.mark.asyncio
async def test_get_by_key_prefix_returns_none_when_ambiguous(test_db):
    """Ambiguous prefixes should not resolve to an arbitrary contact."""
    key1 = "abc1230000000000000000000000000000000000000000000000000000000000"
    key2 = "abc123ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

    await ContactRepository.upsert({"public_key": key1, "name": "A"})
    await ContactRepository.upsert({"public_key": key2, "name": "B"})

    contact = await ContactRepository.get_by_key_prefix("abc123")
    assert contact is None


@pytest.mark.asyncio
async def test_get_by_key_or_prefix_raises_on_ambiguous_prefix(test_db):
    """Prefix lookup should raise when multiple contacts match."""
    key1 = "abc1230000000000000000000000000000000000000000000000000000000000"
    key2 = "abc123ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

    await ContactRepository.upsert({"public_key": key1, "name": "A"})
    await ContactRepository.upsert({"public_key": key2, "name": "B"})

    with pytest.raises(AmbiguousPublicKeyPrefixError):
        await ContactRepository.get_by_key_or_prefix("abc123")


@pytest.mark.asyncio
async def test_get_by_key_or_prefix_prefers_exact_full_key(test_db):
    """Exact key lookup works even when the shorter prefix is ambiguous."""
    key1 = "abc1230000000000000000000000000000000000000000000000000000000000"
    key2 = "abc123ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

    await ContactRepository.upsert({"public_key": key1, "name": "A"})
    await ContactRepository.upsert({"public_key": key2, "name": "B"})

    contact = await ContactRepository.get_by_key_or_prefix(key2.upper())
    assert contact is not None
    assert contact.public_key == key2
