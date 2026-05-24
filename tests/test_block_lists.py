"""Tests for blocked keys and blocked names feature."""

import time

import pytest

from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.routers.settings import (
    BlockKeyRequest,
    BlockNameRequest,
    toggle_blocked_key,
    toggle_blocked_name,
)


class TestBlockListRepository:
    @pytest.mark.asyncio
    async def test_toggle_blocked_key_adds_and_removes(self, test_db):
        result = await AppSettingsRepository.toggle_blocked_key("AABB" * 16)
        assert ("aabb" * 16) in result.blocked_keys

        result = await AppSettingsRepository.toggle_blocked_key("AABB" * 16)
        assert ("aabb" * 16) not in result.blocked_keys

    @pytest.mark.asyncio
    async def test_blocked_key_normalization(self, test_db):
        result = await AppSettingsRepository.toggle_blocked_key("AABBccDD" * 8)
        assert ("aabbccdd" * 8) in result.blocked_keys

    @pytest.mark.asyncio
    async def test_toggle_blocked_name_adds_and_removes(self, test_db):
        result = await AppSettingsRepository.toggle_blocked_name("BadUser")
        assert "BadUser" in result.blocked_names

        result = await AppSettingsRepository.toggle_blocked_name("BadUser")
        assert "BadUser" not in result.blocked_names


class TestBlockListRouterEndpoints:
    @pytest.mark.asyncio
    async def test_toggle_blocked_key_round_trip(self, test_db):
        key = "ff" * 32
        result = await toggle_blocked_key(BlockKeyRequest(key=key))
        assert key in result.blocked_keys

        result = await toggle_blocked_key(BlockKeyRequest(key=key))
        assert key not in result.blocked_keys

    @pytest.mark.asyncio
    async def test_toggle_blocked_name_round_trip(self, test_db):
        result = await toggle_blocked_name(BlockNameRequest(name="Spammer"))
        assert "Spammer" in result.blocked_names

        result = await toggle_blocked_name(BlockNameRequest(name="Spammer"))
        assert "Spammer" not in result.blocked_names


class TestMessageBlockFiltering:
    @pytest.fixture(autouse=True)
    async def _seed_messages(self, test_db):
        """Seed messages for filtering tests."""
        now = int(time.time())
        blocked_key = "aa" * 32
        normal_key = "bb" * 32

        # Incoming DM from blocked key
        await MessageRepository.create(
            msg_type="PRIV",
            text="blocked dm",
            received_at=now,
            conversation_key=blocked_key,
            sender_timestamp=now,
        )

        # Incoming DM from normal key
        await MessageRepository.create(
            msg_type="PRIV",
            text="normal dm",
            received_at=now + 1,
            conversation_key=normal_key,
            sender_timestamp=now + 1,
        )

        # Outgoing DM to blocked key (should NOT be filtered)
        await MessageRepository.create(
            msg_type="PRIV",
            text="outgoing to blocked",
            received_at=now + 2,
            conversation_key=blocked_key,
            sender_timestamp=now + 2,
            outgoing=True,
        )

        # Channel message from blocked name
        await MessageRepository.create(
            msg_type="CHAN",
            text="BlockedName: spam message",
            received_at=now + 3,
            conversation_key="CC" * 16,
            sender_timestamp=now + 3,
            sender_name="BlockedName",
            sender_key="dd" * 32,
        )

        # Channel message from normal sender
        await MessageRepository.create(
            msg_type="CHAN",
            text="NormalUser: hello",
            received_at=now + 4,
            conversation_key="CC" * 16,
            sender_timestamp=now + 4,
            sender_name="NormalUser",
            sender_key=normal_key,
        )

        # Channel message from blocked sender key
        await MessageRepository.create(
            msg_type="CHAN",
            text="AnotherName: also blocked",
            received_at=now + 5,
            conversation_key="CC" * 16,
            sender_timestamp=now + 5,
            sender_name="AnotherName",
            sender_key=blocked_key,
        )

    @pytest.mark.asyncio
    async def test_get_all_filters_blocked_key_dms(self, test_db):
        blocked_key = "aa" * 32
        msgs = await MessageRepository.get_all(blocked_keys=[blocked_key])
        texts = [m.text for m in msgs]
        assert "blocked dm" not in texts
        assert "normal dm" in texts

    @pytest.mark.asyncio
    async def test_get_all_never_filters_outgoing(self, test_db):
        blocked_key = "aa" * 32
        msgs = await MessageRepository.get_all(blocked_keys=[blocked_key])
        texts = [m.text for m in msgs]
        assert "outgoing to blocked" in texts

    @pytest.mark.asyncio
    async def test_get_all_filters_blocked_name(self, test_db):
        msgs = await MessageRepository.get_all(blocked_names=["BlockedName"])
        texts = [m.text for m in msgs]
        assert "BlockedName: spam message" not in texts
        assert "NormalUser: hello" in texts

    @pytest.mark.asyncio
    async def test_get_all_filters_blocked_sender_key_in_channels(self, test_db):
        blocked_key = "aa" * 32
        msgs = await MessageRepository.get_all(blocked_keys=[blocked_key])
        texts = [m.text for m in msgs]
        assert "AnotherName: also blocked" not in texts

    @pytest.mark.asyncio
    async def test_get_around_filters_blocked(self, test_db):
        # Get a normal message ID to center around
        all_msgs = await MessageRepository.get_all()
        normal_msg = next(m for m in all_msgs if m.text == "normal dm")

        blocked_key = "aa" * 32
        msgs, _, _ = await MessageRepository.get_around(
            message_id=normal_msg.id,
            blocked_keys=[blocked_key],
        )
        texts = [m.text for m in msgs]
        assert "blocked dm" not in texts
        assert "normal dm" in texts
        assert "outgoing to blocked" in texts


class TestUnreadCountsBlockFiltering:
    """Unread counts should exclude messages from blocked keys/names."""

    @pytest.mark.asyncio
    async def test_unread_counts_exclude_blocked_key_dms(self, test_db):
        """Blocked key DMs should not contribute to unread counts."""
        blocked_key = "aa" * 32
        normal_key = "bb" * 32
        now = int(time.time())

        # Set up contacts with last_read_at in the past
        await ContactRepository.upsert({"public_key": blocked_key, "name": "Blocked"})
        await ContactRepository.upsert({"public_key": normal_key, "name": "Normal"})

        # Incoming DMs
        await MessageRepository.create(
            msg_type="PRIV",
            text="blocked msg",
            received_at=now,
            conversation_key=blocked_key,
            sender_timestamp=now,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="normal msg",
            received_at=now + 1,
            conversation_key=normal_key,
            sender_timestamp=now + 1,
        )

        result = await MessageRepository.get_unread_counts(
            blocked_keys=[blocked_key],
        )
        assert f"contact-{blocked_key}" not in result["counts"]
        assert result["counts"][f"contact-{normal_key}"] == 1

    @pytest.mark.asyncio
    async def test_unread_counts_exclude_blocked_key_channel_msgs(self, test_db):
        """Blocked key channel messages should not contribute to unread counts."""
        blocked_key = "aa" * 32
        normal_key = "bb" * 32
        chan_key = "CC" * 16
        now = int(time.time())

        await ChannelRepository.upsert(key=chan_key, name="#test")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="CHAN",
            text="Blocked: spam",
            received_at=now,
            conversation_key=chan_key,
            sender_timestamp=now,
            sender_name="Blocked",
            sender_key=blocked_key,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Normal: hi",
            received_at=now + 1,
            conversation_key=chan_key,
            sender_timestamp=now + 1,
            sender_name="Normal",
            sender_key=normal_key,
        )

        result = await MessageRepository.get_unread_counts(
            blocked_keys=[blocked_key],
        )
        assert result["counts"][f"channel-{chan_key}"] == 1

    @pytest.mark.asyncio
    async def test_unread_counts_exclude_blocked_name_channel_msgs(self, test_db):
        """Blocked name channel messages should not contribute to unread counts."""
        chan_key = "DD" * 16
        now = int(time.time())

        await ChannelRepository.upsert(key=chan_key, name="#test2")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="CHAN",
            text="Spammer: buy stuff",
            received_at=now,
            conversation_key=chan_key,
            sender_timestamp=now,
            sender_name="Spammer",
            sender_key="ee" * 32,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Friend: hello",
            received_at=now + 1,
            conversation_key=chan_key,
            sender_timestamp=now + 1,
            sender_name="Friend",
            sender_key="ff" * 32,
        )

        result = await MessageRepository.get_unread_counts(
            blocked_names=["Spammer"],
        )
        assert result["counts"][f"channel-{chan_key}"] == 1

    @pytest.mark.asyncio
    async def test_unread_counts_exclude_blocked_name_dms(self, test_db):
        """Blocked-name DMs should not contribute to unread counts."""
        blocked_key = "aa" * 32
        normal_key = "bb" * 32
        now = int(time.time())

        await ContactRepository.upsert({"public_key": blocked_key, "name": "Spammer"})
        await ContactRepository.upsert({"public_key": normal_key, "name": "Friend"})

        await MessageRepository.create(
            msg_type="PRIV",
            text="blocked dm",
            received_at=now,
            conversation_key=blocked_key,
            sender_timestamp=now,
            sender_name="Spammer",
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="allowed dm",
            received_at=now + 1,
            conversation_key=normal_key,
            sender_timestamp=now + 1,
            sender_name="Friend",
        )

        result = await MessageRepository.get_unread_counts(blocked_names=["Spammer"])

        assert f"contact-{blocked_key}" not in result["counts"]
        assert result["counts"][f"contact-{normal_key}"] == 1

    @pytest.mark.asyncio
    async def test_unread_counts_no_block_lists_returns_all(self, test_db):
        """Without block lists, all messages count toward unreads."""
        blocked_key = "aa" * 32
        chan_key = "CC" * 16
        now = int(time.time())

        await ContactRepository.upsert({"public_key": blocked_key, "name": "Someone"})
        await ChannelRepository.upsert(key=chan_key, name="#all")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="PRIV",
            text="dm",
            received_at=now,
            conversation_key=blocked_key,
            sender_timestamp=now,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Someone: hi",
            received_at=now + 1,
            conversation_key=chan_key,
            sender_timestamp=now + 1,
            sender_name="Someone",
            sender_key=blocked_key,
        )

        result = await MessageRepository.get_unread_counts()
        assert result["counts"][f"contact-{blocked_key}"] == 1
        assert result["counts"][f"channel-{chan_key}"] == 1

    @pytest.mark.asyncio
    async def test_last_message_times_exclude_blocked_key_conversations(self, test_db):
        """Blocked incoming key traffic should not reseed recent-sort timestamps."""
        blocked_key = "aa" * 32
        normal_key = "bb" * 32
        chan_key = "CC" * 16

        await ContactRepository.upsert({"public_key": blocked_key, "name": "Blocked"})
        await ContactRepository.upsert({"public_key": normal_key, "name": "Normal"})
        await ChannelRepository.upsert(key=chan_key, name="#test")

        await MessageRepository.create(
            msg_type="PRIV",
            text="blocked dm",
            received_at=1000,
            conversation_key=blocked_key,
            sender_timestamp=1000,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="normal dm",
            received_at=1001,
            conversation_key=normal_key,
            sender_timestamp=1001,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Blocked: spam",
            received_at=1002,
            conversation_key=chan_key,
            sender_timestamp=1002,
            sender_name="Blocked",
            sender_key=blocked_key,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Normal: hello",
            received_at=1003,
            conversation_key=chan_key,
            sender_timestamp=1003,
            sender_name="Normal",
            sender_key=normal_key,
        )

        result = await MessageRepository.get_unread_counts(blocked_keys=[blocked_key])

        assert f"contact-{blocked_key}" not in result["last_message_times"]
        assert result["last_message_times"][f"contact-{normal_key}"] == 1001
        assert result["last_message_times"][f"channel-{chan_key}"] == 1003

    @pytest.mark.asyncio
    async def test_last_message_times_exclude_blocked_name_channel_msgs(self, test_db):
        """Blocked incoming names should not win the channel's recent timestamp."""
        chan_key = "DD" * 16
        await ChannelRepository.upsert(key=chan_key, name="#test2")

        await MessageRepository.create(
            msg_type="CHAN",
            text="Spammer: buy stuff",
            received_at=2000,
            conversation_key=chan_key,
            sender_timestamp=2000,
            sender_name="Spammer",
            sender_key="ee" * 32,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Friend: hello",
            received_at=1999,
            conversation_key=chan_key,
            sender_timestamp=1999,
            sender_name="Friend",
            sender_key="ff" * 32,
        )

        result = await MessageRepository.get_unread_counts(blocked_names=["Spammer"])

        assert result["last_message_times"][f"channel-{chan_key}"] == 1999

    @pytest.mark.asyncio
    async def test_last_message_times_exclude_blocked_name_dms(self, test_db):
        """Blocked incoming DM names should not reseed recent-sort timestamps."""
        blocked_key = "aa" * 32
        normal_key = "bb" * 32

        await ContactRepository.upsert({"public_key": blocked_key, "name": "Spammer"})
        await ContactRepository.upsert({"public_key": normal_key, "name": "Friend"})

        await MessageRepository.create(
            msg_type="PRIV",
            text="blocked dm",
            received_at=3000,
            conversation_key=blocked_key,
            sender_timestamp=3000,
            sender_name="Spammer",
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="allowed dm",
            received_at=2999,
            conversation_key=normal_key,
            sender_timestamp=2999,
            sender_name="Friend",
        )

        result = await MessageRepository.get_unread_counts(blocked_names=["Spammer"])

        assert f"contact-{blocked_key}" not in result["last_message_times"]
        assert result["last_message_times"][f"contact-{normal_key}"] == 2999
