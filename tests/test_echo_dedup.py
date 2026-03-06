"""Tests for echo detection, ack counting, path accumulation, and dual-path deduplication.

These tests exercise the critical duplicate-handling branches in packet_processor.py
and event_handlers.py that detect mesh echoes, increment ack counts for outgoing
messages, accumulate multi-path routing info, and ensure the dual DM processing
paths (packet_processor + event_handler fallback) don't double-store messages.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.decoder import DecryptedDirectMessage
from app.repository import (
    ContactRepository,
    MessageRepository,
    RawPacketRepository,
)

# Shared test constants
CHANNEL_KEY = "ABC123DEF456ABC123DEF456ABC12345"
CONTACT_PUB = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
OUR_PUB = "FACE123334789E2B81519AFDBC39A3C9EB7EA3457AD367D3243597A484847E46"
SENDER_TIMESTAMP = 1700000000


class TestChannelEchoDetection:
    """Test echo detection for outgoing channel messages.

    When we send a channel message via flood routing, it echoes back through
    repeaters. The duplicate-detection branch in create_message_from_decrypted
    should detect the echo, increment ack_count, and add the echo's path.
    """

    @pytest.mark.asyncio
    async def test_outgoing_echo_increments_ack_and_adds_path(self, test_db, captured_broadcasts):
        """Outgoing channel message echo increments ack count and adds path."""
        from app.packet_processor import create_message_from_decrypted

        # Store the outgoing message (as the send endpoint would)
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="Sender: Hello mesh",
            conversation_key=CHANNEL_KEY,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=True,
        )
        assert msg_id is not None

        # Create a raw packet for the echo
        packet_id, _ = await RawPacketRepository.create(b"echo_packet_1", SENDER_TIMESTAMP + 1)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            # Process the echo (same content, different path)
            result = await create_message_from_decrypted(
                packet_id=packet_id,
                channel_key=CHANNEL_KEY,
                sender="Sender",
                message_text="Hello mesh",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP + 1,
                path="aabb",
            )

        # Should return None (duplicate)
        assert result is None

        # Should broadcast message_acked
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["message_id"] == msg_id
        assert ack_broadcasts[0]["data"]["ack_count"] == 1
        # Path should be in the broadcast
        assert len(ack_broadcasts[0]["data"]["paths"]) >= 1
        assert any(p["path"] == "aabb" for p in ack_broadcasts[0]["data"]["paths"])

        # Should NOT broadcast a new message
        message_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(message_broadcasts) == 0

        # Verify DB state
        msg = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key=CHANNEL_KEY,
            text="Sender: Hello mesh",
            sender_timestamp=SENDER_TIMESTAMP,
        )
        assert msg is not None
        assert msg.acked == 1
        assert msg.paths is not None
        assert any(p.path == "aabb" for p in msg.paths)

    @pytest.mark.asyncio
    async def test_multiple_echoes_increment_progressively(self, test_db, captured_broadcasts):
        """Multiple echoes of the same outgoing message increment ack count progressively."""
        from app.packet_processor import create_message_from_decrypted

        # Store outgoing message
        await MessageRepository.create(
            msg_type="CHAN",
            text="Sender: Flood test",
            conversation_key=CHANNEL_KEY,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=True,
        )

        broadcasts, mock_broadcast = captured_broadcasts
        echo_paths = ["aa", "bbcc", "ddeeff"]

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            for i, path in enumerate(echo_paths):
                pkt_id, _ = await RawPacketRepository.create(
                    f"echo_{i}".encode(), SENDER_TIMESTAMP + i + 1
                )
                await create_message_from_decrypted(
                    packet_id=pkt_id,
                    channel_key=CHANNEL_KEY,
                    sender="Sender",
                    message_text="Flood test",
                    timestamp=SENDER_TIMESTAMP,
                    received_at=SENDER_TIMESTAMP + i + 1,
                    path=path,
                )

        # Should have 3 message_acked broadcasts with increasing counts
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 3
        assert ack_broadcasts[0]["data"]["ack_count"] == 1
        assert ack_broadcasts[1]["data"]["ack_count"] == 2
        assert ack_broadcasts[2]["data"]["ack_count"] == 3

        # Final paths should have all 3 echo paths
        final_paths = ack_broadcasts[2]["data"]["paths"]
        path_values = [p["path"] for p in final_paths]
        for p in echo_paths:
            assert p in path_values

    @pytest.mark.asyncio
    async def test_incoming_duplicate_does_not_increment_ack(self, test_db, captured_broadcasts):
        """Duplicate of incoming (non-outgoing) channel message does NOT increment ack."""
        from app.packet_processor import create_message_from_decrypted

        # First packet creates the incoming message
        pkt1, _ = await RawPacketRepository.create(b"incoming_1", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_message_from_decrypted(
                packet_id=pkt1,
                channel_key=CHANNEL_KEY,
                sender="OtherUser",
                message_text="Incoming msg",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP,
                path="aa",
            )

        assert msg_id is not None

        # Clear broadcasts for the echo
        broadcasts.clear()

        # Second packet is the echo (same content, different path)
        pkt2, _ = await RawPacketRepository.create(b"incoming_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_message_from_decrypted(
                packet_id=pkt2,
                channel_key=CHANNEL_KEY,
                sender="OtherUser",
                message_text="Incoming msg",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP + 1,
                path="bbcc",
            )

        assert result is None

        # Should broadcast message_acked but ack_count should be 0 (not incremented)
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["ack_count"] == 0

        # Path should still be added
        paths = ack_broadcasts[0]["data"]["paths"]
        path_values = [p["path"] for p in paths]
        assert "aa" in path_values
        assert "bbcc" in path_values

    @pytest.mark.asyncio
    async def test_incoming_duplicate_no_path_skips_broadcast(self, test_db, captured_broadcasts):
        """Non-outgoing duplicate with no new path does NOT broadcast message_acked."""
        from app.packet_processor import create_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"inc_np_1", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_message_from_decrypted(
                packet_id=pkt1,
                channel_key=CHANNEL_KEY,
                sender="OtherUser",
                message_text="No path msg",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP,
                path=None,
            )

        assert msg_id is not None
        broadcasts.clear()

        # Duplicate arrives, also with no path
        pkt2, _ = await RawPacketRepository.create(b"inc_np_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_message_from_decrypted(
                packet_id=pkt2,
                channel_key=CHANNEL_KEY,
                sender="OtherUser",
                message_text="No path msg",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP + 1,
                path=None,
            )

        assert result is None

        # No message_acked broadcast — nothing changed
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 0


class TestDMEchoDetection:
    """Test echo detection for direct messages."""

    @pytest.mark.asyncio
    async def test_outgoing_dm_echo_increments_ack(self, test_db, captured_broadcasts):
        """Outgoing DM echo increments ack count."""
        from app.packet_processor import create_dm_message_from_decrypted

        # Store outgoing DM
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Hello friend",
            conversation_key=CONTACT_PUB.lower(),
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=True,
        )
        assert msg_id is not None

        # Echo arrives
        pkt_id, _ = await RawPacketRepository.create(b"dm_echo", SENDER_TIMESTAMP + 1)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Hello friend",
            dest_hash="a1",
            src_hash="fa",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_dm_message_from_decrypted(
                packet_id=pkt_id,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP + 1,
                path="aabb",
                outgoing=True,
            )

        assert result is None

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["ack_count"] == 1
        assert any(p["path"] == "aabb" for p in ack_broadcasts[0]["data"]["paths"])

    @pytest.mark.asyncio
    async def test_incoming_dm_duplicate_does_not_increment_ack(self, test_db, captured_broadcasts):
        """Duplicate of incoming DM does NOT increment ack."""
        from app.packet_processor import create_dm_message_from_decrypted

        # First: create the incoming message
        pkt1, _ = await RawPacketRepository.create(b"dm_in_1", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Hi from mesh",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_dm_message_from_decrypted(
                packet_id=pkt1,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                path="aa",
                outgoing=False,
            )

        assert msg_id is not None
        broadcasts.clear()

        # Duplicate arrives via different path
        pkt2, _ = await RawPacketRepository.create(b"dm_in_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_dm_message_from_decrypted(
                packet_id=pkt2,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP + 1,
                path="bbcc",
                outgoing=False,
            )

        assert result is None

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["ack_count"] == 0  # NOT incremented

        # Path still added
        paths = ack_broadcasts[0]["data"]["paths"]
        path_values = [p["path"] for p in paths]
        assert "bbcc" in path_values

    @pytest.mark.asyncio
    async def test_incoming_dm_duplicate_no_path_skips_broadcast(
        self, test_db, captured_broadcasts
    ):
        """Non-outgoing DM duplicate with no new path does NOT broadcast message_acked."""
        from app.packet_processor import create_dm_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"dm_np_1", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="No path DM",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_dm_message_from_decrypted(
                packet_id=pkt1,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                path=None,
                outgoing=False,
            )

        assert msg_id is not None
        broadcasts.clear()

        # Duplicate arrives, also with no path
        pkt2, _ = await RawPacketRepository.create(b"dm_np_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_dm_message_from_decrypted(
                packet_id=pkt2,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP + 1,
                path=None,
                outgoing=False,
            )

        assert result is None

        # No message_acked broadcast — nothing changed
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 0


class TestDualPathDedup:
    """Test deduplication between the packet_processor and event_handler fallback paths.

    DMs can be processed by two paths:
    1. Primary: RX_LOG_DATA → packet_processor (decrypts with private key)
    2. Fallback: CONTACT_MSG_RECV → on_contact_message (MeshCore library decoded)

    The fallback uses INSERT OR IGNORE to avoid double-storage when both fire.
    """

    @pytest.mark.asyncio
    async def test_event_handler_deduplicates_against_packet_processor(
        self, test_db, captured_broadcasts
    ):
        """on_contact_message does not double-store when packet_processor already handled it."""
        from app.event_handlers import on_contact_message
        from app.packet_processor import create_dm_message_from_decrypted

        # 1) Packet processor stores the message first
        pkt_id, _ = await RawPacketRepository.create(b"primary_path", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Dedup test message",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_dm_message_from_decrypted(
                packet_id=pkt_id,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                outgoing=False,
            )

        assert msg_id is not None

        # Record broadcast count after packet_processor
        broadcasts_after_primary = len(broadcasts)

        # 2) Event handler fires with the same message content
        mock_event = MagicMock()
        mock_event.payload = {
            "public_key": CONTACT_PUB,
            "text": "Dedup test message",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
        }

        # Mock contact lookup to return a contact with the right key
        mock_contact = MagicMock()
        mock_contact.public_key = CONTACT_PUB
        mock_contact.type = 1  # Client, not repeater
        mock_contact.name = "TestContact"

        with (
            patch("app.event_handlers.ContactRepository") as mock_contact_repo,
            patch("app.event_handlers.broadcast_event", mock_broadcast),
        ):
            mock_contact_repo.get_by_key_or_prefix = AsyncMock(return_value=mock_contact)
            mock_contact_repo.update_last_contacted = AsyncMock()

            await on_contact_message(mock_event)

        # No additional message broadcast should have been sent
        new_message_broadcasts = [
            b for b in broadcasts[broadcasts_after_primary:] if b["type"] == "message"
        ]
        assert len(new_message_broadcasts) == 0

        # Only one message in DB
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_case_consistency_between_paths(self, test_db, captured_broadcasts):
        """Event handler lowercases conversation_key to match packet_processor.

        This tests the fix applied to event_handlers.py where contact.public_key
        is now lowercased before being used as conversation_key.
        """
        from app.event_handlers import on_contact_message
        from app.packet_processor import create_dm_message_from_decrypted

        # Use an uppercase key to exercise the case sensitivity path
        upper_key = CONTACT_PUB.upper()

        # 1) Packet processor stores with lowercased key (always)
        pkt_id, _ = await RawPacketRepository.create(b"case_test", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Case sensitivity test",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_dm_message_from_decrypted(
                packet_id=pkt_id,
                decrypted=decrypted,
                their_public_key=upper_key,  # Uppercase input
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                outgoing=False,
            )

        assert msg_id is not None

        # Verify it was stored with lowercase key
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=upper_key.lower(), limit=10
        )
        assert len(messages) == 1

        broadcasts_after_primary = len(broadcasts)

        # 2) Event handler fires - contact DB returns uppercase key
        mock_event = MagicMock()
        mock_event.payload = {
            "public_key": upper_key,
            "text": "Case sensitivity test",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
        }

        mock_contact = MagicMock()
        mock_contact.public_key = upper_key  # Uppercase from DB
        mock_contact.type = 1
        mock_contact.name = "TestContact"

        with (
            patch("app.event_handlers.ContactRepository") as mock_contact_repo,
            patch("app.event_handlers.broadcast_event", mock_broadcast),
        ):
            mock_contact_repo.get_by_key_or_prefix = AsyncMock(return_value=mock_contact)
            mock_contact_repo.update_last_contacted = AsyncMock()

            await on_contact_message(mock_event)

        # Should NOT create a second message (dedup catches it thanks to .lower())
        new_message_broadcasts = [
            b for b in broadcasts[broadcasts_after_primary:] if b["type"] == "message"
        ]
        assert len(new_message_broadcasts) == 0

        # Still only one message in DB
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=upper_key.lower(), limit=10
        )
        assert len(messages) == 1


class TestDirectMessageDirectionDetection:
    """Test src_hash/dest_hash direction detection in _process_direct_message.

    The packet processor uses the first byte of public keys to determine
    message direction. This is a subtle 1-byte hash comparison with an
    ambiguous case when both bytes match (1/256 chance).
    """

    OUR_PUB_BYTES = bytes.fromhex(OUR_PUB)
    OUR_FIRST_BYTE = format(OUR_PUB_BYTES[0], "02x")  # "fa"

    # Contact whose first byte differs from ours
    DIFFERENT_CONTACT_PUB = CONTACT_PUB  # starts with "a1"
    DIFFERENT_FIRST_BYTE = "a1"

    # Contact whose first byte matches ours ("fa...")
    SAME_BYTE_CONTACT_PUB = (
        "fa" + "b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"[2:]
    )

    @pytest.mark.asyncio
    async def test_incoming_message_detected(self, test_db, captured_broadcasts):
        """dest_hash matches us, src_hash doesn't → incoming."""
        from app.packet_processor import _process_direct_message

        # Build a minimal packet_info where payload has [dest_hash=fa, src_hash=a1, ...]
        packet_info = MagicMock()
        packet_info.payload = bytes([0xFA, 0xA1, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""

        # Create the contact so decryption can find a candidate
        await ContactRepository.upsert(
            {
                "public_key": self.DIFFERENT_CONTACT_PUB,
                "name": "TestContact",
                "type": 1,
            }
        )

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Incoming test",
            dest_hash=self.OUR_FIRST_BYTE,
            src_hash=self.DIFFERENT_FIRST_BYTE,
        )

        pkt_id, _ = await RawPacketRepository.create(b"dir_test_in", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=self.OUR_PUB_BYTES),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        assert result is not None
        assert result["decrypted"] is True

        # Message should be stored as incoming (outgoing=False)
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.DIFFERENT_CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].outgoing is False

    @pytest.mark.asyncio
    async def test_incoming_zero_hop_dm_preserves_empty_path(self, test_db, captured_broadcasts):
        """A 0-hop DM should preserve path='' rather than dropping path data."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        packet_info.payload = bytes([0xFA, 0xA1, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""

        await ContactRepository.upsert(
            {
                "public_key": self.DIFFERENT_CONTACT_PUB,
                "name": "TestContact",
                "type": 1,
            }
        )

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Zero hop DM",
            dest_hash=self.OUR_FIRST_BYTE,
            src_hash=self.DIFFERENT_FIRST_BYTE,
        )

        pkt_id, _ = await RawPacketRepository.create(b"dir_test_zero_hop", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=self.OUR_PUB_BYTES),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        assert result is not None

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.DIFFERENT_CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].paths is not None
        assert len(messages[0].paths) == 1
        assert messages[0].paths[0].path == ""

        message_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(message_broadcasts) == 1
        assert message_broadcasts[0]["data"]["paths"] == [
            {"path": "", "received_at": SENDER_TIMESTAMP}
        ]

    @pytest.mark.asyncio
    async def test_outgoing_message_detected(self, test_db, captured_broadcasts):
        """src_hash matches us, dest_hash doesn't → outgoing."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        # dest_hash=a1 (contact), src_hash=fa (us)
        packet_info.payload = bytes([0xA1, 0xFA, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""

        await ContactRepository.upsert(
            {
                "public_key": self.DIFFERENT_CONTACT_PUB,
                "name": "TestContact",
                "type": 1,
            }
        )

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Outgoing test",
            dest_hash=self.DIFFERENT_FIRST_BYTE,
            src_hash=self.OUR_FIRST_BYTE,
        )

        pkt_id, _ = await RawPacketRepository.create(b"dir_test_out", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=self.OUR_PUB_BYTES),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        assert result is not None

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.DIFFERENT_CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].outgoing is True

    @pytest.mark.asyncio
    async def test_ambiguous_direction_defaults_to_incoming(self, test_db, captured_broadcasts):
        """Both hash bytes match us → ambiguous → defaults to incoming."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        # Both dest_hash and src_hash are 0xFA (our first byte)
        packet_info.payload = bytes([0xFA, 0xFA, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""

        # Contact whose first byte also starts with "fa"
        await ContactRepository.upsert(
            {
                "public_key": self.SAME_BYTE_CONTACT_PUB,
                "name": "SameByteContact",
                "type": 1,
            }
        )

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Ambiguous direction",
            dest_hash=self.OUR_FIRST_BYTE,
            src_hash=self.OUR_FIRST_BYTE,
        )

        pkt_id, _ = await RawPacketRepository.create(b"dir_test_ambig", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=self.OUR_PUB_BYTES),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        assert result is not None

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.SAME_BYTE_CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].outgoing is False  # Defaults to incoming

    @pytest.mark.asyncio
    async def test_neither_hash_matches_returns_none(self, test_db, captured_broadcasts):
        """Neither hash byte matches us → not our message → returns None."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        # Neither byte matches our first byte (0xFA)
        packet_info.payload = bytes([0x11, 0x22, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""

        pkt_id, _ = await RawPacketRepository.create(b"dir_test_none", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=self.OUR_PUB_BYTES),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        # Not our message - should return None without attempting decryption
        assert result is None


class TestConcurrentDMDedup:
    """Test that concurrent DM processing deduplicates via atomic INSERT OR IGNORE.

    On a mesh network, the same DM packet can arrive via two RF paths nearly
    simultaneously, causing two concurrent calls to create_dm_message_from_decrypted.
    SQLite's INSERT OR IGNORE ensures only one message is stored.
    """

    @pytest.mark.asyncio
    async def test_concurrent_identical_dms_only_store_once(self, test_db, captured_broadcasts):
        """Two concurrent create_dm_message_from_decrypted calls with identical content
        should result in exactly one stored message."""
        from app.packet_processor import create_dm_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"concurrent_dm_1", SENDER_TIMESTAMP)
        pkt2, _ = await RawPacketRepository.create(b"concurrent_dm_2", SENDER_TIMESTAMP + 1)

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Concurrent dedup test",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            results = await asyncio.gather(
                create_dm_message_from_decrypted(
                    packet_id=pkt1,
                    decrypted=decrypted,
                    their_public_key=CONTACT_PUB,
                    our_public_key=OUR_PUB,
                    received_at=SENDER_TIMESTAMP,
                    path="aa",
                    outgoing=False,
                ),
                create_dm_message_from_decrypted(
                    packet_id=pkt2,
                    decrypted=decrypted,
                    their_public_key=CONTACT_PUB,
                    our_public_key=OUR_PUB,
                    received_at=SENDER_TIMESTAMP + 1,
                    path="bbcc",
                    outgoing=False,
                ),
            )

        # Exactly one should create, the other should return None (duplicate)
        created = [r for r in results if r is not None]
        duplicates = [r for r in results if r is None]
        assert len(created) == 1
        assert len(duplicates) == 1

        # Only one message in DB
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_concurrent_channel_echoes_only_store_once(self, test_db, captured_broadcasts):
        """Two concurrent create_message_from_decrypted calls with identical content
        should result in exactly one stored message."""
        from app.packet_processor import create_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"concurrent_chan_1", SENDER_TIMESTAMP)
        pkt2, _ = await RawPacketRepository.create(b"concurrent_chan_2", SENDER_TIMESTAMP + 1)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            results = await asyncio.gather(
                create_message_from_decrypted(
                    packet_id=pkt1,
                    channel_key=CHANNEL_KEY,
                    sender="Alice",
                    message_text="Concurrent channel test",
                    timestamp=SENDER_TIMESTAMP,
                    received_at=SENDER_TIMESTAMP,
                    path="aa",
                ),
                create_message_from_decrypted(
                    packet_id=pkt2,
                    channel_key=CHANNEL_KEY,
                    sender="Alice",
                    message_text="Concurrent channel test",
                    timestamp=SENDER_TIMESTAMP,
                    received_at=SENDER_TIMESTAMP + 1,
                    path="bbcc",
                ),
            )

        created = [r for r in results if r is not None]
        duplicates = [r for r in results if r is None]
        assert len(created) == 1
        assert len(duplicates) == 1

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=CHANNEL_KEY, limit=10
        )
        assert len(messages) == 1


class TestMessageAckedBroadcastShape:
    """Verify that message_acked broadcasts from _handle_duplicate_message
    match the frontend's MessageAckedEvent interface.

    The on_ack handler (event_handlers.py) broadcasts {message_id, ack_count},
    while _handle_duplicate_message broadcasts {message_id, ack_count, paths}.
    Both must match what the frontend expects in useWebSocket.ts.
    """

    # Frontend MessageAckedEvent keys (from useWebSocket.ts:113-117)
    # The 'paths' key is optional in the TypeScript interface
    REQUIRED_KEYS = {"message_id", "ack_count"}
    OPTIONAL_KEYS = {"paths"}

    @pytest.mark.asyncio
    async def test_outgoing_echo_broadcast_shape(self, test_db, captured_broadcasts):
        """Outgoing echo broadcast has all required keys plus paths."""
        from app.packet_processor import create_message_from_decrypted

        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="Sender: Shape test",
            conversation_key=CHANNEL_KEY,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=True,
        )

        pkt_id, _ = await RawPacketRepository.create(b"shape_echo", SENDER_TIMESTAMP + 1)
        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await create_message_from_decrypted(
                packet_id=pkt_id,
                channel_key=CHANNEL_KEY,
                sender="Sender",
                message_text="Shape test",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP + 1,
                path="aabb",
            )

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1

        payload = ack_broadcasts[0]["data"]
        payload_keys = set(payload.keys())

        # Must have all required keys
        assert payload_keys >= self.REQUIRED_KEYS
        # Must only have expected keys
        assert payload_keys <= (self.REQUIRED_KEYS | self.OPTIONAL_KEYS)

        # Verify types
        assert isinstance(payload["message_id"], int)
        assert isinstance(payload["ack_count"], int)
        assert payload["message_id"] == msg_id
        assert payload["ack_count"] == 1

        # paths should be a list of dicts with path and received_at keys
        assert isinstance(payload["paths"], list)
        for p in payload["paths"]:
            assert "path" in p
            assert "received_at" in p

    @pytest.mark.asyncio
    async def test_incoming_echo_broadcast_shape(self, test_db, captured_broadcasts):
        """Incoming echo broadcast (with path) has the correct shape."""
        from app.packet_processor import create_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"shape_inc_1", SENDER_TIMESTAMP)

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await create_message_from_decrypted(
                packet_id=pkt1,
                channel_key=CHANNEL_KEY,
                sender="Other",
                message_text="Incoming shape",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP,
                path="aa",
            )

        broadcasts.clear()

        pkt2, _ = await RawPacketRepository.create(b"shape_inc_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await create_message_from_decrypted(
                packet_id=pkt2,
                channel_key=CHANNEL_KEY,
                sender="Other",
                message_text="Incoming shape",
                timestamp=SENDER_TIMESTAMP,
                received_at=SENDER_TIMESTAMP + 1,
                path="bbcc",
            )

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1

        payload = ack_broadcasts[0]["data"]
        payload_keys = set(payload.keys())

        assert payload_keys >= self.REQUIRED_KEYS
        assert payload_keys <= (self.REQUIRED_KEYS | self.OPTIONAL_KEYS)
        assert payload["ack_count"] == 0  # Not outgoing, no ack increment

    @pytest.mark.asyncio
    async def test_dm_echo_broadcast_shape(self, test_db, captured_broadcasts):
        """DM duplicate broadcast has the same shape as channel echo."""
        from app.packet_processor import create_dm_message_from_decrypted

        pkt1, _ = await RawPacketRepository.create(b"dm_shape_1", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="DM shape test",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            msg_id = await create_dm_message_from_decrypted(
                packet_id=pkt1,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                outgoing=True,
                path="aabb",
            )

        assert msg_id is not None
        broadcasts.clear()

        pkt2, _ = await RawPacketRepository.create(b"dm_shape_2", SENDER_TIMESTAMP + 1)

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await create_dm_message_from_decrypted(
                packet_id=pkt2,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP + 1,
                outgoing=True,
                path="ccddee",
            )

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1

        payload = ack_broadcasts[0]["data"]
        payload_keys = set(payload.keys())

        assert payload_keys >= self.REQUIRED_KEYS
        assert payload_keys <= (self.REQUIRED_KEYS | self.OPTIONAL_KEYS)
        assert isinstance(payload["message_id"], int)
        assert isinstance(payload["ack_count"], int)
        assert payload["ack_count"] == 1  # Outgoing DM echo increments ack
