"""Tests for DM ACK tracking wiring in the send_direct_message endpoint.

Verifies that expected_ack from the radio result is correctly extracted,
hex-encoded, and passed to track_pending_ack.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

import app.services.message_send as message_send_service
from app.models import SendDirectMessageRequest
from app.radio import radio_manager
from app.repository import ContactRepository
from app.routers.messages import send_direct_message


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


@pytest.fixture(autouse=True)
def _disable_background_dm_retries(monkeypatch):
    monkeypatch.setattr(message_send_service, "DM_SEND_MAX_ATTEMPTS", 1)
    yield


def _make_mc(name="TestNode"):
    mc = MagicMock()
    mc.self_info = {"name": name}
    mc.commands = MagicMock()
    mc.commands.add_contact = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.get_contact_by_key_prefix = MagicMock(return_value=None)
    return mc


async def _insert_contact(public_key, name="Alice"):
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": 0,
            "flags": 0,
            "direct_path": None,
            "direct_path_len": -1,
            "direct_path_hash_mode": -1,
            "last_advert": None,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": False,
            "last_contacted": None,
        }
    )


class TestDMAckTrackingWiring:
    """Verify that send_direct_message correctly wires ACK tracking."""

    @pytest.mark.asyncio
    async def test_expected_ack_bytes_tracked_as_hex(self, test_db):
        """expected_ack bytes from radio are hex-encoded and tracked."""
        mc = _make_mc()
        ack_bytes = b"\xde\xad\xbe\xef"

        result = MagicMock()
        result.type = EventType.MSG_SENT
        result.payload = {
            "expected_ack": ack_bytes,
            "suggested_timeout": 8000,
        }
        mc.commands.send_msg = AsyncMock(return_value=result)

        pub_key = "aa" * 32
        await _insert_contact(pub_key)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.track_pending_ack") as mock_track,
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            message = await send_direct_message(request)
            await asyncio.sleep(0)

            mock_track.assert_called_once_with(
                "deadbeef",  # hex-encoded ack bytes
                message.id,
                8000,  # suggested_timeout
            )

    @pytest.mark.asyncio
    async def test_expected_ack_string_tracked_directly(self, test_db):
        """expected_ack already a string is passed without hex conversion."""
        mc = _make_mc()

        result = MagicMock()
        result.type = EventType.MSG_SENT
        result.payload = {
            "expected_ack": "abcdef01",
            "suggested_timeout": 5000,
        }
        mc.commands.send_msg = AsyncMock(return_value=result)

        pub_key = "bb" * 32
        await _insert_contact(pub_key)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.track_pending_ack") as mock_track,
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            message = await send_direct_message(request)
            await asyncio.sleep(0)

            mock_track.assert_called_once_with(
                "abcdef01",
                message.id,
                5000,
            )

    @pytest.mark.asyncio
    async def test_missing_expected_ack_skips_tracking(self, test_db):
        """No ACK tracking when expected_ack is missing from result payload."""
        mc = _make_mc()

        result = MagicMock()
        result.type = EventType.MSG_SENT
        result.payload = {}  # no expected_ack
        mc.commands.send_msg = AsyncMock(return_value=result)

        pub_key = "cc" * 32
        await _insert_contact(pub_key)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.track_pending_ack") as mock_track,
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            await send_direct_message(request)
            await asyncio.sleep(0)

            mock_track.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_timeout_used_when_missing(self, test_db):
        """Default 10000ms timeout used when suggested_timeout is missing."""
        mc = _make_mc()

        result = MagicMock()
        result.type = EventType.MSG_SENT
        result.payload = {
            "expected_ack": b"\x01\x02\x03\x04",
            # no suggested_timeout
        }
        mc.commands.send_msg = AsyncMock(return_value=result)

        pub_key = "dd" * 32
        await _insert_contact(pub_key)

        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.messages.track_pending_ack") as mock_track,
            patch("app.routers.messages.broadcast_event"),
        ):
            request = SendDirectMessageRequest(destination=pub_key, text="Hello")
            message = await send_direct_message(request)
            await asyncio.sleep(0)

            mock_track.assert_called_once_with(
                "01020304",
                message.id,
                10000,  # default
            )
