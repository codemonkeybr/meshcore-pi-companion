"""Tests for the channels router endpoints."""

import time
from hashlib import sha256
from unittest.mock import AsyncMock, patch

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME
from app.repository import ChannelRepository, MessageRepository


class TestChannelFloodScopeOverride:
    @pytest.mark.asyncio
    async def test_sets_channel_flood_scope_override(self, test_db, client):
        key = "AA" * 16
        await ChannelRepository.upsert(key=key, name="#flightless", is_hashtag=True)

        with patch("app.routers.channels.broadcast_event") as mock_broadcast:
            response = await client.post(
                f"/api/channels/{key}/flood-scope-override",
                json={"flood_scope_override": "Esperance"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["flood_scope_override"] == "#Esperance"

        channel = await ChannelRepository.get_by_key(key)
        assert channel is not None
        assert channel.flood_scope_override == "#Esperance"
        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args.args[0] == "channel"


class TestCreateChannel:
    @pytest.mark.asyncio
    async def test_create_broadcasts_channel_update(self, test_db):
        from app.routers.channels import CreateChannelRequest, create_channel

        with patch("app.routers.channels.broadcast_event") as mock_broadcast:
            result = await create_channel(CreateChannelRequest(name="#mychannel"))

        mock_broadcast.assert_called_once()
        assert mock_broadcast.call_args.args[0] == "channel"
        assert mock_broadcast.call_args.args[1]["key"] == result.key

    @pytest.mark.asyncio
    async def test_existing_hash_is_not_doubled(self, test_db, client):
        key = "CC" * 16
        await ChannelRepository.upsert(key=key, name="#flightless", is_hashtag=True)

        response = await client.post(
            f"/api/channels/{key}/flood-scope-override",
            json={"flood_scope_override": "#Esperance"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["flood_scope_override"] == "#Esperance"

    @pytest.mark.asyncio
    async def test_blank_override_clears_channel_flood_scope_override(self, test_db, client):
        key = "BB" * 16
        await ChannelRepository.upsert(key=key, name="#flightless", is_hashtag=True)
        await ChannelRepository.update_flood_scope_override(key, "#Esperance")

        response = await client.post(
            f"/api/channels/{key}/flood-scope-override",
            json={"flood_scope_override": "   "},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["flood_scope_override"] is None

        channel = await ChannelRepository.get_by_key(key)
        assert channel is not None
        assert channel.flood_scope_override is None

    @pytest.mark.asyncio
    async def test_bulk_hashtag_create_adds_only_new_rooms(self, test_db, client):
        ops_key = sha256(b"#ops").digest()[:16].hex().upper()
        await ChannelRepository.upsert(key=ops_key, name="#ops", is_hashtag=True)

        response = await client.post(
            "/api/channels/bulk-hashtag",
            json={
                "channel_names": ["#ops", "mesh-room", "bad_room", "mesh-room", "another-room"],
                "try_historical": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert [channel["name"] for channel in data["created_channels"]] == [
            "#mesh-room",
            "#another-room",
        ]
        assert data["existing_count"] == 2
        assert data["invalid_names"] == ["bad_room"]
        assert data["decrypt_started"] is False

    @pytest.mark.asyncio
    async def test_bulk_hashtag_create_can_start_one_decrypt_job(self, test_db, client):
        with (
            patch(
                "app.routers.channels.RawPacketRepository.get_undecrypted_count",
                new=AsyncMock(return_value=7),
            ),
            patch(
                "app.routers.channels._run_historical_channel_decryption_for_channels",
                new=AsyncMock(),
            ) as mock_decrypt,
        ):
            response = await client.post(
                "/api/channels/bulk-hashtag",
                json={
                    "channel_names": ["ops", "mesh-room"],
                    "try_historical": True,
                },
            )

        assert response.status_code == 202
        data = response.json()
        assert data["decrypt_started"] is True
        assert data["decrypt_total_packets"] == 7
        mock_decrypt.assert_awaited_once()


class TestPublicChannelProtection:
    @pytest.mark.asyncio
    async def test_create_public_uses_canonical_key(self, test_db):
        from app.routers.channels import CreateChannelRequest, create_channel

        result = await create_channel(CreateChannelRequest(name="Public"))

        assert result.key == PUBLIC_CHANNEL_KEY
        assert result.name == PUBLIC_CHANNEL_NAME
        assert result.is_hashtag is False

    @pytest.mark.asyncio
    async def test_create_public_rejects_conflicting_key(self, test_db, client):
        response = await client.post(
            "/api/channels",
            json={"name": "Public", "key": "AA" * 16},
        )

        assert response.status_code == 400
        assert "canonical Public key" in response.json()["detail"]
        assert await ChannelRepository.get_by_key("AA" * 16) is None

    @pytest.mark.asyncio
    async def test_create_non_public_rejects_public_key(self, test_db, client):
        response = await client.post(
            "/api/channels",
            json={"name": "Ops", "key": PUBLIC_CHANNEL_KEY},
        )

        assert response.status_code == 400
        assert PUBLIC_CHANNEL_NAME in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_public_channel_is_rejected(self, test_db, client):
        await ChannelRepository.upsert(
            key=PUBLIC_CHANNEL_KEY,
            name=PUBLIC_CHANNEL_NAME,
            is_hashtag=False,
            on_radio=False,
        )

        response = await client.delete(f"/api/channels/{PUBLIC_CHANNEL_KEY}")

        assert response.status_code == 400
        assert "cannot be deleted" in response.json()["detail"]
        channel = await ChannelRepository.get_by_key(PUBLIC_CHANNEL_KEY)
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
