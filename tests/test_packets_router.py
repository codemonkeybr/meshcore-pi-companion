"""Tests for the packets router.

Covers the historical channel decryption endpoint, background task,
undecrypted count endpoint, and the maintenance endpoint.
"""

import time
from unittest.mock import patch

import pytest

from app.repository import ChannelRepository, MessageRepository, RawPacketRepository


async def _insert_raw_packets(count: int, decrypted: bool = False, age_days: int = 0) -> list[int]:
    """Insert raw packets and return their IDs."""
    ids = []
    base_ts = int(time.time()) - (age_days * 86400)
    for i in range(count):
        packet_id, _ = await RawPacketRepository.create(
            f"packet_data_{i}_{age_days}_{decrypted}".encode(), base_ts + i
        )
        if decrypted:
            # Create a message and link it
            msg_id = await MessageRepository.create(
                msg_type="CHAN",
                text=f"decrypted msg {i}",
                conversation_key="DEADBEEF" * 4,
                sender_timestamp=base_ts + i,
                received_at=base_ts + i,
            )
            if msg_id is not None:
                await RawPacketRepository.mark_decrypted(packet_id, msg_id)
        ids.append(packet_id)
    return ids


class TestUndecryptedCount:
    """Test GET /api/packets/undecrypted/count."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_empty(self, test_db, client):
        response = await client.get("/api/packets/undecrypted/count")

        assert response.status_code == 200
        assert response.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_counts_only_undecrypted(self, test_db, client):
        await _insert_raw_packets(3, decrypted=False)
        await _insert_raw_packets(2, decrypted=True)

        response = await client.get("/api/packets/undecrypted/count")

        assert response.status_code == 200
        assert response.json()["count"] == 3


class TestGetRawPacket:
    """Test GET /api/packets/{id}."""

    @pytest.mark.asyncio
    async def test_returns_404_when_missing(self, test_db, client):
        response = await client.get("/api/packets/999999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_returns_linked_packet_details(self, test_db, client):
        channel_key = "DEADBEEF" * 4
        await ChannelRepository.upsert(key=channel_key, name="#ops", is_hashtag=False)
        packet_id, _ = await RawPacketRepository.create(b"\x09\x00test-packet", 1700000000)
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: hello",
            conversation_key=channel_key,
            sender_timestamp=1700000000,
            received_at=1700000000,
            sender_name="Alice",
        )
        assert msg_id is not None
        await RawPacketRepository.mark_decrypted(packet_id, msg_id)

        response = await client.get(f"/api/packets/{packet_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == packet_id
        assert data["timestamp"] == 1700000000
        assert data["data"] == "0900746573742d7061636b6574"
        assert data["decrypted"] is True
        assert data["decrypted_info"] == {
            "channel_name": "#ops",
            "sender": "Alice",
            "channel_key": channel_key,
            "contact_key": None,
            "sender_timestamp": 1700000000,
            "message": "Alice: hello",
        }


class TestDecryptHistoricalPackets:
    """Test POST /api/packets/decrypt/historical."""

    @pytest.mark.asyncio
    async def test_channel_decrypt_with_hex_key(self, test_db, client):
        """Channel decryption with a valid hex key starts background task."""
        await _insert_raw_packets(5)

        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_key": "0123456789abcdef0123456789abcdef",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["started"] is True
        assert data["total_packets"] == 5
        assert "background" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_channel_decrypt_with_hashtag_name(self, test_db, client):
        """Channel decryption with a channel name derives key from hash."""
        await _insert_raw_packets(3)

        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_name": "#general",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["started"] is True
        assert data["total_packets"] == 3

    @pytest.mark.asyncio
    async def test_channel_decrypt_invalid_hex(self, test_db, client):
        """Invalid hex string for channel key returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_key": "not_valid_hex",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "invalid" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_channel_decrypt_wrong_key_length(self, test_db, client):
        """Channel key with wrong length returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_key": "aabbccdd",  # Only 4 bytes, need 16
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "16 bytes" in data["detail"]

    @pytest.mark.asyncio
    async def test_channel_decrypt_no_key_or_name(self, test_db, client):
        """Channel decryption without key or name returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={"key_type": "channel"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "must provide" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_channel_decrypt_no_undecrypted_packets(self, test_db, client):
        """Channel decryption with no undecrypted packets returns not started."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_key": "0123456789abcdef0123456789abcdef",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["started"] is False
        assert data["total_packets"] == 0

    @pytest.mark.asyncio
    async def test_channel_decrypt_resolves_channel_name(self, test_db, client):
        """Channel decryption finds display name from DB when channel exists."""
        key_hex = "0123456789ABCDEF0123456789ABCDEF"
        await ChannelRepository.upsert(key=key_hex, name="#test-channel", is_hashtag=True)
        await _insert_raw_packets(1)

        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "channel",
                "channel_key": key_hex.lower(),
            },
        )

        assert response.status_code == 202
        assert response.json()["started"] is True

    @pytest.mark.asyncio
    async def test_contact_decrypt_missing_private_key(self, test_db, client):
        """Contact decryption without private key returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "contact",
                "contact_public_key": "aa" * 32,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "private_key" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_contact_decrypt_missing_contact_key(self, test_db, client):
        """Contact decryption without contact public key returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "contact",
                "private_key": "aa" * 64,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "contact_public_key" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_contact_decrypt_wrong_private_key_length(self, test_db, client):
        """Private key with wrong length returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "contact",
                "private_key": "aa" * 32,  # 32 bytes, need 64
                "contact_public_key": "bb" * 32,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "64 bytes" in data["detail"]

    @pytest.mark.asyncio
    async def test_contact_decrypt_wrong_public_key_length(self, test_db, client):
        """Contact public key with wrong length returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "contact",
                "private_key": "aa" * 64,
                "contact_public_key": "bb" * 16,  # 16 bytes, need 32
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "32 bytes" in data["detail"]

    @pytest.mark.asyncio
    async def test_contact_decrypt_invalid_hex(self, test_db, client):
        """Invalid hex for private key returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={
                "key_type": "contact",
                "private_key": "zz" * 64,
                "contact_public_key": "bb" * 32,
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "invalid" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_key_type(self, test_db, client):
        """Invalid key_type returns error."""
        response = await client.post(
            "/api/packets/decrypt/historical",
            json={"key_type": "invalid"},
        )

        assert response.status_code == 400
        data = response.json()
        assert "key_type" in data["detail"].lower()


class TestUndecryptedTextPacketStreaming:
    @pytest.mark.asyncio
    async def test_count_undecrypted_text_messages_uses_keyset_pagination(self, test_db):
        """Counting undecrypted DM packets should use keyset pagination and filter by payload type."""

        # Simulate keyset pagination: each execute() call returns a cursor
        # whose fetchall() yields one batch.  The generator stops when a
        # batch is empty.
        batches = [
            [
                {"id": 1, "data": b"\x09\x00dm", "timestamp": 1000},
                {"id": 2, "data": b"\x15\x00chan", "timestamp": 1001},
            ],
            [{"id": 3, "data": b"\x09\x00dm2", "timestamp": 1002}],
            [],
        ]

        def fake_execute(*_args, **_kwargs):
            batch = batches.pop(0)

            class FakeCursor:
                async def fetchall(self):
                    return batch

                async def close(self):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return None

            # aiosqlite's execute() returns a `contextmanager`-decorated
            # coroutine that is both awaitable and usable as an async-with.
            # Our repo code now uses `async with conn.execute(...) as cursor:`,
            # so the mock just needs to return something with __aenter__/__aexit__.
            return FakeCursor()

        with patch.object(test_db.conn, "execute", side_effect=fake_execute):
            count = await RawPacketRepository.count_undecrypted_text_messages(batch_size=2)

        # header byte 0x09 -> payload type 2 (TEXT_MESSAGE); 0x15 -> type 5 (not TEXT_MESSAGE)
        assert count == 2


class TestRunHistoricalChannelDecryption:
    """Test the _run_historical_channel_decryption background task."""

    @pytest.mark.asyncio
    async def test_decrypts_matching_packets(self, test_db):
        """Background task decrypts packets that match the channel key."""
        from app.routers.packets import _run_historical_channel_decryption

        # Insert undecrypted packets
        await _insert_raw_packets(3)
        channel_key_hex = "AABBCCDDAABBCCDDAABBCCDDAABBCCDD"
        channel_key_bytes = bytes.fromhex(channel_key_hex)

        # Each packet must have unique content to avoid message deduplication
        call_count = 0

        def make_unique_result(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            return type(
                "DecryptResult",
                (),
                {
                    "sender": f"User{call_count}",
                    "message": f"Hello {call_count}",
                    "timestamp": 1700000000 + call_count,
                },
            )()

        with (
            patch(
                "app.routers.packets.try_decrypt_packet_with_channel_key",
                side_effect=make_unique_result,
            ),
            patch(
                "app.routers.packets.parse_packet",
                return_value=None,
            ),
            patch("app.routers.packets.broadcast_success") as mock_success,
        ):
            await _run_historical_channel_decryption(channel_key_bytes, channel_key_hex, "#test")

        mock_success.assert_called_once()
        assert "3" in mock_success.call_args[0][1]  # "Decrypted 3 messages"

    @pytest.mark.asyncio
    async def test_skips_non_matching_packets(self, test_db):
        """Background task skips packets that don't match the channel key."""
        from app.routers.packets import _run_historical_channel_decryption

        await _insert_raw_packets(2)
        channel_key_hex = "AABBCCDDAABBCCDDAABBCCDDAABBCCDD"
        channel_key_bytes = bytes.fromhex(channel_key_hex)

        with (
            patch(
                "app.routers.packets.try_decrypt_packet_with_channel_key",
                return_value=None,  # No match
            ),
            patch("app.routers.packets.broadcast_success") as mock_success,
        ):
            await _run_historical_channel_decryption(channel_key_bytes, channel_key_hex, "#test")

        # No success broadcast when nothing was decrypted
        mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_packets_returns_early(self, test_db):
        """Background task returns early when no undecrypted packets exist."""
        from app.routers.packets import _run_historical_channel_decryption

        channel_key_hex = "AABBCCDDAABBCCDDAABBCCDDAABBCCDD"
        channel_key_bytes = bytes.fromhex(channel_key_hex)

        with patch("app.routers.packets.broadcast_success") as mock_success:
            await _run_historical_channel_decryption(channel_key_bytes, channel_key_hex)

        mock_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_display_name_fallback(self, test_db):
        """Uses channel key prefix when no display name is provided."""
        from app.routers.packets import _run_historical_channel_decryption

        await _insert_raw_packets(1)
        channel_key_hex = "AABBCCDDAABBCCDDAABBCCDDAABBCCDD"
        channel_key_bytes = bytes.fromhex(channel_key_hex)

        mock_result = type(
            "DecryptResult",
            (),
            {
                "sender": "User",
                "message": "msg",
                "timestamp": 1700000000,
            },
        )()

        with (
            patch(
                "app.routers.packets.try_decrypt_packet_with_channel_key",
                return_value=mock_result,
            ),
            patch("app.routers.packets.parse_packet", return_value=None),
            patch("app.routers.packets.broadcast_success") as mock_success,
        ):
            await _run_historical_channel_decryption(
                channel_key_bytes,
                channel_key_hex,
                None,  # No display name
            )

        # Should use key prefix as display name
        call_msg = mock_success.call_args[0][0]
        assert channel_key_hex[:12] in call_msg


class TestMaintenanceEndpoint:
    """Test POST /api/packets/maintenance."""

    @pytest.mark.asyncio
    async def test_prune_old_undecrypted(self, test_db, client):
        """Prune deletes undecrypted packets older than threshold."""
        await _insert_raw_packets(3, decrypted=False, age_days=30)
        await _insert_raw_packets(2, decrypted=False, age_days=0)

        response = await client.post(
            "/api/packets/maintenance",
            json={"prune_undecrypted_days": 7},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["packets_deleted"] == 3

        # Verify only recent packets remain
        remaining = await RawPacketRepository.get_undecrypted_count()
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_purge_linked_raw_packets(self, test_db, client):
        """Purge deletes raw packets that are linked to stored messages."""
        await _insert_raw_packets(3, decrypted=True)
        await _insert_raw_packets(2, decrypted=False)

        response = await client.post(
            "/api/packets/maintenance",
            json={"purge_linked_raw_packets": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["packets_deleted"] == 3

        # Undecrypted packets should remain
        remaining = await RawPacketRepository.get_undecrypted_count()
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_both_prune_and_purge(self, test_db, client):
        """Both prune and purge can run in a single request."""
        await _insert_raw_packets(2, decrypted=True)
        await _insert_raw_packets(3, decrypted=False, age_days=30)
        await _insert_raw_packets(1, decrypted=False, age_days=0)

        response = await client.post(
            "/api/packets/maintenance",
            json={
                "prune_undecrypted_days": 7,
                "purge_linked_raw_packets": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        # 2 linked + 3 old undecrypted = 5 deleted
        assert data["packets_deleted"] == 5

    @pytest.mark.asyncio
    async def test_no_options_deletes_nothing(self, test_db, client):
        """No options specified means no deletions (only vacuum)."""
        await _insert_raw_packets(5)

        response = await client.post(
            "/api/packets/maintenance",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["packets_deleted"] == 0

    @pytest.mark.asyncio
    async def test_vacuum_reports_status(self, test_db, client):
        """Maintenance endpoint reports vacuum status."""
        response = await client.post(
            "/api/packets/maintenance",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        # vacuumed is a boolean (may be True or False depending on DB state)
        assert isinstance(data["vacuumed"], bool)

    @pytest.mark.asyncio
    async def test_prune_days_validation(self, test_db, client):
        """prune_undecrypted_days must be >= 1."""
        response = await client.post(
            "/api/packets/maintenance",
            json={"prune_undecrypted_days": 0},
        )

        assert response.status_code == 422
