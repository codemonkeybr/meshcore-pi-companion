"""Tests for echo detection, ack counting, path accumulation, and dual-path deduplication.

These tests exercise the critical duplicate-handling branches in packet_processor.py
and event_handlers.py that detect mesh echoes, increment ack counts for outgoing
messages, accumulate multi-path routing info, and ensure the dual DM processing
paths (packet_processor + event_handler fallback) don't double-store messages.
"""

import asyncio
from unittest.mock import MagicMock, patch

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
    async def test_outgoing_dm_echo_adds_path_without_incrementing_ack(
        self, test_db, captured_broadcasts
    ):
        """Outgoing DM duplicate keeps path updates but does not count as delivery evidence."""
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
        assert ack_broadcasts[0]["data"]["message_id"] == msg_id
        assert ack_broadcasts[0]["data"]["ack_count"] == 0
        assert any(p["path"] == "aabb" for p in ack_broadcasts[0]["data"]["paths"])

        msg = await MessageRepository.get_by_content(
            msg_type="PRIV",
            conversation_key=CONTACT_PUB.lower(),
            text="Hello friend",
            sender_timestamp=SENDER_TIMESTAMP,
        )
        assert msg is not None
        assert msg.acked == 0
        assert msg.paths is not None
        assert any(p.path == "aabb" for p in msg.paths)

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

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_dm_message_from_decrypted(
                packet_id=pkt1,
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

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            result = await create_dm_message_from_decrypted(
                packet_id=pkt1,
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

    The fallback path should reconcile against the packet path instead of creating
    a second row, and should still add new path observations when available.
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

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
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

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
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

    @pytest.mark.asyncio
    async def test_event_handler_duplicate_adds_path_to_existing_dm(
        self, test_db, captured_broadcasts
    ):
        """Fallback DM duplicates should reconcile path updates onto the stored message."""
        from app.event_handlers import on_contact_message
        from app.packet_processor import create_dm_message_from_decrypted

        await ContactRepository.upsert(
            {
                "public_key": CONTACT_PUB.lower(),
                "name": "TestContact",
                "type": 1,
                "last_seen": SENDER_TIMESTAMP,
                "last_contacted": SENDER_TIMESTAMP,
                "first_seen": SENDER_TIMESTAMP,
                "on_radio": False,
            }
        )

        pkt_id, _ = await RawPacketRepository.create(b"primary_with_no_path", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Dual path with route update",
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
        broadcasts.clear()

        mock_event = MagicMock()
        mock_event.payload = {
            "public_key": CONTACT_PUB,
            "text": "Dual path with route update",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
            "path": "bbcc",
            "path_len": 2,
        }

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
            await on_contact_message(mock_event)

        message_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert message_broadcasts == []

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["message_id"] == msg_id
        assert ack_broadcasts[0]["data"]["ack_count"] == 0
        assert any(p["path"] == "bbcc" for p in ack_broadcasts[0]["data"]["paths"])

        msg = await MessageRepository.get_by_id(msg_id)
        assert msg is not None
        assert msg.paths is not None
        assert any(p.path == "bbcc" for p in msg.paths)

    @pytest.mark.asyncio
    async def test_incoming_duplicate_does_not_reconcile_onto_matching_outgoing_dm(
        self, test_db, captured_broadcasts
    ):
        """Incoming DM duplicates must merge onto the incoming row, not a sent row."""
        from app.event_handlers import on_contact_message
        from app.packet_processor import create_dm_message_from_decrypted

        await ContactRepository.upsert(
            {
                "public_key": CONTACT_PUB.lower(),
                "name": "TestContact",
                "type": 1,
                "last_seen": SENDER_TIMESTAMP,
                "last_contacted": SENDER_TIMESTAMP,
                "first_seen": SENDER_TIMESTAMP,
                "on_radio": False,
            }
        )

        outgoing_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Mirror text",
            conversation_key=CONTACT_PUB.lower(),
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP - 1,
            outgoing=True,
        )
        assert outgoing_id is not None

        pkt_id, _ = await RawPacketRepository.create(b"incoming_primary", SENDER_TIMESTAMP)
        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Mirror text",
            dest_hash="fa",
            src_hash="a1",
        )

        broadcasts, mock_broadcast = captured_broadcasts

        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            incoming_id = await create_dm_message_from_decrypted(
                packet_id=pkt_id,
                decrypted=decrypted,
                their_public_key=CONTACT_PUB,
                our_public_key=OUR_PUB,
                received_at=SENDER_TIMESTAMP,
                outgoing=False,
            )

        assert incoming_id is not None
        broadcasts.clear()

        mock_event = MagicMock()
        mock_event.payload = {
            "public_key": CONTACT_PUB,
            "text": "Mirror text",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
            "path": "bbcc",
            "path_len": 2,
        }

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
            await on_contact_message(mock_event)

        incoming_msg = await MessageRepository.get_by_id(incoming_id)
        outgoing_msg = await MessageRepository.get_by_id(outgoing_id)
        assert incoming_msg is not None
        assert outgoing_msg is not None
        assert incoming_msg.paths is not None
        assert any(p.path == "bbcc" for p in incoming_msg.paths)
        assert outgoing_msg.paths is None

    @pytest.mark.asyncio
    async def test_fallback_path_duplicate_reconciles_path_without_new_row(
        self, test_db, captured_broadcasts
    ):
        """Repeated fallback DMs should keep one row and merge path observations."""
        from app.event_handlers import on_contact_message

        await ContactRepository.upsert(
            {
                "public_key": CONTACT_PUB.lower(),
                "name": "FallbackOnly",
                "type": 1,
                "last_seen": SENDER_TIMESTAMP,
                "last_contacted": SENDER_TIMESTAMP,
                "first_seen": SENDER_TIMESTAMP,
                "on_radio": False,
            }
        )

        broadcasts, mock_broadcast = captured_broadcasts

        first_event = MagicMock()
        first_event.payload = {
            "public_key": CONTACT_PUB,
            "text": "Fallback duplicate route test",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
        }

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
            await on_contact_message(first_event)

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1
        msg_id = messages[0].id

        broadcasts.clear()

        second_event = MagicMock()
        second_event.payload = {
            "public_key": CONTACT_PUB,
            "text": "Fallback duplicate route test",
            "txt_type": 0,
            "sender_timestamp": SENDER_TIMESTAMP,
            "path": "ddee",
            "path_len": 2,
        }

        with patch("app.event_handlers.broadcast_event", mock_broadcast):
            await on_contact_message(second_event)

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=CONTACT_PUB.lower(), limit=10
        )
        assert len(messages) == 1

        message_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert message_broadcasts == []

        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["message_id"] == msg_id
        assert ack_broadcasts[0]["data"]["ack_count"] == 0
        assert any(p["path"] == "ddee" for p in ack_broadcasts[0]["data"]["paths"])


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
        packet_info.path_length = 0

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
        packet_info.path_length = 0

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
            {"path": "", "received_at": SENDER_TIMESTAMP, "path_len": 0, "rssi": None, "snr": None}
        ]

    @pytest.mark.asyncio
    async def test_outgoing_message_detected(self, test_db, captured_broadcasts):
        """src_hash matches us, dest_hash doesn't → outgoing."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        # dest_hash=a1 (contact), src_hash=fa (us)
        packet_info.payload = bytes([0xA1, 0xFA, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""
        packet_info.path_length = 0

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
        packet_info.path_length = 0

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
    async def test_ambiguous_direction_resolves_outgoing_echo(self, test_db, captured_broadcasts):
        """Ambiguous direction resolves to outgoing when a matching sent message exists.

        Uses real colliding keys where both public keys start with 0xAA.
        Without the fix, the echo would be stored as a second (incoming) row.
        """
        from app.packet_processor import _process_direct_message

        our_pub = "AAAA09479CF6FD6733CF052769E7C229CB86CA7F81E82439F9E4EB832CA7F8DC"
        contact_pub = "AAAA2A563964F9B66E25E81FE6931B0E72AF585AEF79F43C1364DB4F6F882F07"
        our_pub_bytes = bytes.fromhex(our_pub)
        first_byte = "aa"

        await ContactRepository.upsert(
            {"public_key": contact_pub, "name": "CollidingContact", "type": 1}
        )

        # The send endpoint already stored the outgoing message
        outgoing_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Echo collision test",
            conversation_key=contact_pub.lower(),
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=True,
        )
        assert outgoing_id is not None

        packet_info = MagicMock()
        packet_info.payload = bytes([0xAA, 0xAA, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b"\xbb"
        packet_info.path_length = 1

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Echo collision test",
            dest_hash=first_byte,
            src_hash=first_byte,
        )

        pkt_id, _ = await RawPacketRepository.create(b"ambig_echo", SENDER_TIMESTAMP + 1)
        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=our_pub_bytes),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP + 1, packet_info
            )

        assert result is not None

        # Should have exactly one message — the original outgoing, not a ghost incoming
        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=contact_pub.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].outgoing is True
        assert messages[0].id == outgoing_id

        # Path from the echo should have been added to the outgoing message
        ack_broadcasts = [b for b in broadcasts if b["type"] == "message_acked"]
        assert len(ack_broadcasts) == 1
        assert ack_broadcasts[0]["data"]["message_id"] == outgoing_id
        assert any(p["path"] == "bb" for p in ack_broadcasts[0]["data"]["paths"])

    @pytest.mark.asyncio
    async def test_ambiguous_direction_genuine_incoming_still_stored(
        self, test_db, captured_broadcasts
    ):
        """Ambiguous direction with no matching outgoing message stores as incoming."""
        from app.packet_processor import _process_direct_message

        our_pub = "AAAA09479CF6FD6733CF052769E7C229CB86CA7F81E82439F9E4EB832CA7F8DC"
        contact_pub = "AAAA2A563964F9B66E25E81FE6931B0E72AF585AEF79F43C1364DB4F6F882F07"
        our_pub_bytes = bytes.fromhex(our_pub)
        first_byte = "aa"

        await ContactRepository.upsert(
            {"public_key": contact_pub, "name": "CollidingContact", "type": 1}
        )

        # No outgoing message exists — this is a genuine incoming DM
        packet_info = MagicMock()
        packet_info.payload = bytes([0xAA, 0xAA, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""
        packet_info.path_length = 0

        decrypted = DecryptedDirectMessage(
            timestamp=SENDER_TIMESTAMP,
            flags=0,
            message="Genuine incoming",
            dest_hash=first_byte,
            src_hash=first_byte,
        )

        pkt_id, _ = await RawPacketRepository.create(b"ambig_genuine", SENDER_TIMESTAMP)
        broadcasts, mock_broadcast = captured_broadcasts

        with (
            patch("app.packet_processor.has_private_key", return_value=True),
            patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
            patch("app.packet_processor.get_public_key", return_value=our_pub_bytes),
            patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
            patch("app.packet_processor.broadcast_event", mock_broadcast),
        ):
            result = await _process_direct_message(
                b"\x00" * 40, pkt_id, SENDER_TIMESTAMP, packet_info
            )

        assert result is not None

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=contact_pub.lower(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].outgoing is False  # Still incoming when no outgoing match

    @pytest.mark.asyncio
    async def test_neither_hash_matches_returns_none(self, test_db, captured_broadcasts):
        """Neither hash byte matches us → not our message → returns None."""
        from app.packet_processor import _process_direct_message

        packet_info = MagicMock()
        # Neither byte matches our first byte (0xFA)
        packet_info.payload = bytes([0x11, 0x22, 0x00, 0x00]) + b"\x00" * 20
        packet_info.path = b""
        packet_info.path_length = 0

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
    """Test that concurrent DM processing deduplicates by raw-packet identity.

    On a mesh network, the same DM payload can be observed twice before the first
    handler finishes. Both arrivals reuse the same raw_packets row and should end
    up attached to a single message.
    """

    @pytest.mark.asyncio
    async def test_concurrent_same_packet_dms_only_store_once(self, test_db, captured_broadcasts):
        """Two concurrent handlers for the same raw DM packet store one message."""
        from app.packet_processor import create_dm_message_from_decrypted

        packet_id, _ = await RawPacketRepository.create(b"concurrent_dm_1", SENDER_TIMESTAMP)

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
                    packet_id=packet_id,
                    decrypted=decrypted,
                    their_public_key=CONTACT_PUB,
                    our_public_key=OUR_PUB,
                    received_at=SENDER_TIMESTAMP,
                    path="aa",
                    outgoing=False,
                ),
                create_dm_message_from_decrypted(
                    packet_id=packet_id,
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
    OPTIONAL_KEYS = {"paths", "packet_id"}

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
        assert payload["packet_id"] == pkt_id

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
        assert payload["packet_id"] == pkt1

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
        assert payload["ack_count"] == 0  # Outgoing DM duplicates no longer count as delivery
        assert payload["packet_id"] == pkt1


class TestRoomServerMessageDedup:
    """Test that room-server posts from different authors are not collapsed.

    Room messages are PRIV type sharing one conversation_key (the room contact's
    pubkey).  The dedup index includes sender_key so that two different room
    participants sending identical text in the same clock second are stored as
    separate messages.
    """

    ROOM_PUB = "bb" * 32  # Room contact public key
    SENDER_A_KEY = "aa" * 32
    SENDER_B_KEY = "cc" * 32

    @pytest.mark.asyncio
    async def test_distinct_room_authors_same_text_same_second_stored_separately(self, test_db):
        """Two room users sending identical text in the same second produce two rows."""
        msg_id_a = await MessageRepository.create(
            msg_type="PRIV",
            text="ok",
            conversation_key=self.ROOM_PUB,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=False,
            sender_key=self.SENDER_A_KEY,
            sender_name="Alice",
        )
        assert msg_id_a is not None

        msg_id_b = await MessageRepository.create(
            msg_type="PRIV",
            text="ok",
            conversation_key=self.ROOM_PUB,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP + 1,
            outgoing=False,
            sender_key=self.SENDER_B_KEY,
            sender_name="Bob",
        )
        assert msg_id_b is not None, (
            "Second room post with different sender_key should not be deduped"
        )
        assert msg_id_a != msg_id_b

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.ROOM_PUB, limit=10
        )
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_same_room_author_same_text_same_second_still_deduped(self, test_db):
        """True echo from the same room author is still collapsed (same sender_key)."""
        msg_id_1 = await MessageRepository.create(
            msg_type="PRIV",
            text="ok",
            conversation_key=self.ROOM_PUB,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=False,
            sender_key=self.SENDER_A_KEY,
            sender_name="Alice",
        )
        assert msg_id_1 is not None

        msg_id_2 = await MessageRepository.create(
            msg_type="PRIV",
            text="ok",
            conversation_key=self.ROOM_PUB,
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP + 1,
            outgoing=False,
            sender_key=self.SENDER_A_KEY,
            sender_name="Alice",
        )
        assert msg_id_2 is None, "Same sender_key should still be deduped"

        messages = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=self.ROOM_PUB, limit=10
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_null_sender_key_still_dedupes_normally(self, test_db):
        """Non-room incoming DMs (sender_key=None) still dedupe on content."""
        msg_id_1 = await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key=CONTACT_PUB.lower(),
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP,
            outgoing=False,
            sender_key=None,
        )
        assert msg_id_1 is not None

        msg_id_2 = await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key=CONTACT_PUB.lower(),
            sender_timestamp=SENDER_TIMESTAMP,
            received_at=SENDER_TIMESTAMP + 1,
            outgoing=False,
            sender_key=None,
        )
        assert msg_id_2 is None, "Both NULL sender_key should still collide"
