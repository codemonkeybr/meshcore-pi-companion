"""Tests for the channels router endpoints.

Covers POST /api/channels/sync (radio sync) and GET /api/channels/{key}/detail
(channel stats).
"""

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

from app.radio import radio_manager
from app.repository import ChannelRepository, MessageRepository


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


def _make_channel_info(name: str, secret: bytes):
    """Create a mock channel info response."""
    result = MagicMock()
    result.type = EventType.CHANNEL_INFO
    result.payload = {
        "channel_name": name,
        "channel_secret": secret,
    }
    return result


def _make_empty_channel():
    """Create a mock empty channel response."""
    result = MagicMock()
    result.type = EventType.CHANNEL_INFO
    result.payload = {
        "channel_name": "\x00\x00\x00\x00",
        "channel_secret": b"",
    }
    return result


def _make_error_response():
    """Create a mock error response (channel slot unused)."""
    result = MagicMock()
    result.type = EventType.ERROR
    result.payload = {}
    return result


@asynccontextmanager
async def _noop_radio_operation(mc):
    """No-op radio_operation context manager that yields mc."""
    yield mc


class TestSyncChannelsFromRadio:
    """Test POST /api/channels/sync."""

    @pytest.mark.asyncio
    async def test_sync_channels_basic(self, test_db, client):
        """Sync creates channels from radio slots."""
        secret_a = bytes.fromhex("0123456789abcdef0123456789abcdef")
        secret_b = bytes.fromhex("fedcba9876543210fedcba9876543210")

        mock_mc = MagicMock()

        async def mock_get_channel(idx):
            if idx == 0:
                return _make_channel_info("#general", secret_a)
            if idx == 1:
                return _make_channel_info("Private", secret_b)
            return _make_empty_channel()

        mock_mc.commands.get_channel = AsyncMock(side_effect=mock_get_channel)
        radio_manager._meshcore = mock_mc

        with (
            patch("app.dependencies.radio_manager") as mock_dep_rm,
            patch("app.routers.channels.radio_manager") as mock_ch_rm,
        ):
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc
            mock_ch_rm.radio_operation = lambda desc: _noop_radio_operation(mock_mc)

            response = await client.post("/api/channels/sync?max_channels=5")

        assert response.status_code == 200
        data = response.json()
        assert data["synced"] == 2

        # Verify channels in DB (2 synced + #remoteterm seed)
        channels = await ChannelRepository.get_all()
        assert len(channels) == 3

        keys = {ch.key for ch in channels}
        assert secret_a.hex().upper() in keys
        assert secret_b.hex().upper() in keys

    @pytest.mark.asyncio
    async def test_sync_skips_empty_channels(self, test_db, client):
        """Empty channel slots are skipped during sync."""
        secret = bytes.fromhex("aabbccddaabbccddaabbccddaabbccdd")
        mock_mc = MagicMock()

        async def mock_get_channel(idx):
            if idx == 0:
                return _make_channel_info("#test", secret)
            return _make_empty_channel()

        mock_mc.commands.get_channel = AsyncMock(side_effect=mock_get_channel)
        radio_manager._meshcore = mock_mc

        with (
            patch("app.dependencies.radio_manager") as mock_dep_rm,
            patch("app.routers.channels.radio_manager") as mock_ch_rm,
        ):
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc
            mock_ch_rm.radio_operation = lambda desc: _noop_radio_operation(mock_mc)

            response = await client.post("/api/channels/sync?max_channels=5")

        assert response.status_code == 200
        assert response.json()["synced"] == 1

    @pytest.mark.asyncio
    async def test_sync_hashtag_flag(self, test_db, client):
        """Channels starting with # are marked as hashtag channels."""
        secret = bytes.fromhex("1122334455667788aabbccddeeff0011")
        mock_mc = MagicMock()

        async def mock_get_channel(idx):
            if idx == 0:
                return _make_channel_info("#hashtag-room", secret)
            return _make_empty_channel()

        mock_mc.commands.get_channel = AsyncMock(side_effect=mock_get_channel)
        radio_manager._meshcore = mock_mc

        with (
            patch("app.dependencies.radio_manager") as mock_dep_rm,
            patch("app.routers.channels.radio_manager") as mock_ch_rm,
        ):
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc
            mock_ch_rm.radio_operation = lambda desc: _noop_radio_operation(mock_mc)

            response = await client.post("/api/channels/sync?max_channels=3")

        assert response.status_code == 200

        channel = await ChannelRepository.get_by_key(secret.hex().upper())
        assert channel is not None
        assert channel.is_hashtag is True
        assert channel.name == "#hashtag-room"
        assert channel.on_radio is True

    @pytest.mark.asyncio
    async def test_sync_marks_channels_on_radio(self, test_db, client):
        """Synced channels have on_radio=True."""
        secret = bytes.fromhex("aabbccddaabbccddaabbccddaabbccdd")
        mock_mc = MagicMock()

        async def mock_get_channel(idx):
            if idx == 0:
                return _make_channel_info("MyChannel", secret)
            return _make_empty_channel()

        mock_mc.commands.get_channel = AsyncMock(side_effect=mock_get_channel)
        radio_manager._meshcore = mock_mc

        with (
            patch("app.dependencies.radio_manager") as mock_dep_rm,
            patch("app.routers.channels.radio_manager") as mock_ch_rm,
        ):
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc
            mock_ch_rm.radio_operation = lambda desc: _noop_radio_operation(mock_mc)

            await client.post("/api/channels/sync?max_channels=3")

        channel = await ChannelRepository.get_by_key(secret.hex().upper())
        assert channel.on_radio is True

    @pytest.mark.asyncio
    async def test_sync_requires_connection(self, test_db, client):
        """Sync returns 503 when radio is not connected."""
        with patch("app.dependencies.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            response = await client.post("/api/channels/sync")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_sync_key_normalized_uppercase(self, test_db, client):
        """Channel keys are normalized to uppercase hex."""
        secret = bytes.fromhex("aabbccddaabbccddaabbccddaabbccdd")
        mock_mc = MagicMock()

        async def mock_get_channel(idx):
            if idx == 0:
                return _make_channel_info("Test", secret)
            return _make_empty_channel()

        mock_mc.commands.get_channel = AsyncMock(side_effect=mock_get_channel)
        radio_manager._meshcore = mock_mc

        with (
            patch("app.dependencies.radio_manager") as mock_dep_rm,
            patch("app.routers.channels.radio_manager") as mock_ch_rm,
        ):
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc
            mock_ch_rm.radio_operation = lambda desc: _noop_radio_operation(mock_mc)

            await client.post("/api/channels/sync?max_channels=3")

        channel = await ChannelRepository.get_by_key("AABBCCDDAABBCCDDAABBCCDDAABBCCDD")
        assert channel is not None


class TestChannelDetail:
    """Test GET /api/channels/{key}/detail."""

    CHANNEL_KEY = "AABBCCDDAABBCCDDAABBCCDDAABBCCDD"

    async def _seed_channel(self):
        """Create a channel in the DB."""
        await ChannelRepository.upsert(
            key=self.CHANNEL_KEY,
            name="#test-channel",
            is_hashtag=True,
            on_radio=True,
        )

    async def _insert_message(
        self,
        conversation_key: str,
        text: str,
        received_at: int,
        sender_key: str | None = None,
        sender_name: str | None = None,
    ) -> int | None:
        return await MessageRepository.create(
            msg_type="CHAN",
            text=text,
            received_at=received_at,
            conversation_key=conversation_key,
            sender_key=sender_key,
            sender_name=sender_name,
        )

    @pytest.mark.asyncio
    async def test_detail_basic_stats(self, test_db, client):
        """Channel with messages returns correct counts."""
        await self._seed_channel()
        now = int(time.time())
        # Insert messages at different ages
        await self._insert_message(self.CHANNEL_KEY, "recent1", now - 60, "aaa", "Alice")
        await self._insert_message(self.CHANNEL_KEY, "recent2", now - 120, "bbb", "Bob")
        await self._insert_message(self.CHANNEL_KEY, "old", now - 90000, "aaa", "Alice")

        response = await client.get(f"/api/channels/{self.CHANNEL_KEY}/detail")
        assert response.status_code == 200
        data = response.json()

        assert data["channel"]["key"] == self.CHANNEL_KEY
        assert data["channel"]["name"] == "#test-channel"
        assert data["message_counts"]["all_time"] == 3
        assert data["message_counts"]["last_1h"] == 2
        assert data["unique_sender_count"] == 2
        assert data["first_message_at"] == now - 90000

    @pytest.mark.asyncio
    async def test_detail_404_unknown_key(self, test_db, client):
        """Unknown channel key returns 404."""
        response = await client.get("/api/channels/FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF/detail")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_empty_stats(self, test_db, client):
        """Channel with no messages returns zeroed stats."""
        await self._seed_channel()

        response = await client.get(f"/api/channels/{self.CHANNEL_KEY}/detail")
        assert response.status_code == 200
        data = response.json()

        assert data["message_counts"]["all_time"] == 0
        assert data["message_counts"]["last_1h"] == 0
        assert data["unique_sender_count"] == 0
        assert data["first_message_at"] is None
        assert data["top_senders_24h"] == []

    @pytest.mark.asyncio
    async def test_detail_time_window_bucketing(self, test_db, client):
        """Messages at different ages fall into correct time buckets."""
        await self._seed_channel()
        now = int(time.time())

        # 30 min ago → last_1h, last_24h, last_48h, last_7d
        await self._insert_message(self.CHANNEL_KEY, "m1", now - 1800, "aaa")
        # 2 hours ago → last_24h, last_48h, last_7d (not last_1h)
        await self._insert_message(self.CHANNEL_KEY, "m2", now - 7200, "bbb")
        # 30 hours ago → last_48h, last_7d (not last_1h or last_24h)
        await self._insert_message(self.CHANNEL_KEY, "m3", now - 108000, "ccc")
        # 3 days ago → last_7d only
        await self._insert_message(self.CHANNEL_KEY, "m4", now - 259200, "ddd")
        # 10 days ago → all_time only
        await self._insert_message(self.CHANNEL_KEY, "m5", now - 864000, "eee")

        response = await client.get(f"/api/channels/{self.CHANNEL_KEY}/detail")
        data = response.json()
        counts = data["message_counts"]

        assert counts["last_1h"] == 1
        assert counts["last_24h"] == 2
        assert counts["last_48h"] == 3
        assert counts["last_7d"] == 4
        assert counts["all_time"] == 5

    @pytest.mark.asyncio
    async def test_detail_top_senders_ordering(self, test_db, client):
        """Top senders are ordered by message count descending."""
        await self._seed_channel()
        now = int(time.time())

        # Alice: 3 messages, Bob: 1 message
        for i in range(3):
            await self._insert_message(
                self.CHANNEL_KEY, f"alice-{i}", now - 60 * (i + 1), "aaa", "Alice"
            )
        await self._insert_message(self.CHANNEL_KEY, "bob-1", now - 300, "bbb", "Bob")

        response = await client.get(f"/api/channels/{self.CHANNEL_KEY}/detail")
        data = response.json()

        senders = data["top_senders_24h"]
        assert len(senders) == 2
        assert senders[0]["sender_name"] == "Alice"
        assert senders[0]["message_count"] == 3
        assert senders[1]["sender_name"] == "Bob"
        assert senders[1]["message_count"] == 1
