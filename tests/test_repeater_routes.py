"""Tests for repeater-specific contacts routes (telemetry, command, trace)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.database import Database
from app.models import CommandRequest, TelemetryRequest
from app.repository import ContactRepository
from app.routers.contacts import (
    _fetch_repeater_response,
    request_telemetry,
    request_trace,
    send_repeater_command,
)

KEY_A = "aa" * 32

# Patch target for the wall-clock wrapper used by _fetch_repeater_response.
# We patch _monotonic (not time.monotonic) to avoid breaking the asyncio event loop.
_MONOTONIC = "app.routers.contacts._monotonic"


@pytest.fixture
async def test_db():
    """Create an in-memory test database with schema + migrations."""
    import app.repository as repo_module

    db = Database(":memory:")
    await db.connect()

    original_db = repo_module.db
    repo_module.db = db

    try:
        yield db
    finally:
        repo_module.db = original_db
        await db.disconnect()


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


async def _insert_contact(public_key: str, name: str = "Node", contact_type: int = 0):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
            "flags": 0,
            "last_path": None,
            "last_path_len": -1,
            "last_advert": None,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": False,
            "last_contacted": None,
        }
    )


def _mock_mc():
    mc = MagicMock()
    mc.commands = MagicMock()
    mc.commands.req_status_sync = AsyncMock()
    mc.commands.fetch_all_neighbours = AsyncMock()
    mc.commands.req_acl_sync = AsyncMock()
    mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.get_msg = AsyncMock()
    mc.commands.add_contact = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
    mc.wait_for_event = AsyncMock()
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    return mc


def _advancing_clock(start=0.0, step=0.1):
    """Return a callable for _monotonic that advances by `step` each call."""
    t = start

    def _tick():
        nonlocal t
        val = t
        t += step
        return val

    return _tick


class TestFetchRepeaterResponse:
    """Tests for the _fetch_repeater_response helper."""

    @pytest.mark.asyncio
    async def test_returns_matching_cli_response(self):
        mc = _mock_mc()
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
            )
        )

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"
        mc.commands.get_msg.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_same_sender_non_cli_message(self):
        """A txt_type=0 message from the target repeater is NOT accepted as the CLI response."""
        mc = _mock_mc()
        non_cli = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "chat msg", "txt_type": 0},
        )
        cli_response = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[non_cli, cli_response])

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"
        assert mc.commands.get_msg.await_count == 2

    @pytest.mark.asyncio
    async def test_unrelated_dm_is_skipped(self):
        """Unrelated DMs are skipped (dispatcher already handled them)."""
        mc = _mock_mc()
        unrelated = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "bbbbbbbbbbbb", "text": "hello", "txt_type": 0},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[unrelated, expected])

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"

    @pytest.mark.asyncio
    async def test_channel_message_is_skipped(self):
        mc = _mock_mc()
        channel_msg = _radio_result(
            EventType.CHANNEL_MSG_RECV,
            {"channel_idx": 0, "text": "flood msg"},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[channel_msg, expected])

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"

    @pytest.mark.asyncio
    async def test_no_more_msgs_retries_then_succeeds(self):
        mc = _mock_mc()
        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[no_msgs, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"
        assert mc.commands.get_msg.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_after_deadline(self):
        """Returns None when wall-clock deadline expires."""
        mc = _mock_mc()
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        # Start at 100.0, jump past deadline (timeout=2.0) after 2 get_msg calls
        times = iter([100.0, 100.5, 101.0, 103.0])

        with (
            patch(_MONOTONIC, side_effect=times),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=2.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_error_retries_then_succeeds(self):
        mc = _mock_mc()
        error = _radio_result(EventType.ERROR, {"err": "busy"})
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[error, expected])

        with (
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=5.0)

        assert result is not None
        assert result.payload["text"] == "ok"

    @pytest.mark.asyncio
    async def test_high_traffic_does_not_exhaust_budget(self):
        """Many unrelated messages don't prevent eventual success (wall-clock deadline)."""
        mc = _mock_mc()
        # 20 unrelated DMs followed by the expected CLI response
        unrelated = [
            _radio_result(
                EventType.CONTACT_MSG_RECV,
                {"pubkey_prefix": f"{i:012x}", "text": f"msg {i}", "txt_type": 0},
            )
            for i in range(20)
        ]
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "aaaaaaaaaaaa", "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[*unrelated, expected])

        with patch(_MONOTONIC, side_effect=_advancing_clock()):
            result = await _fetch_repeater_response(mc, "aaaaaaaaaaaa", timeout=30.0)

        assert result is not None
        assert result.payload["text"] == "ver 1.0"
        assert mc.commands.get_msg.await_count == 21


class TestTelemetryRoute:
    @pytest.mark.asyncio
    async def test_returns_404_when_contact_missing(self, test_db):
        mc = _mock_mc()
        with patch("app.routers.contacts.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await request_telemetry(KEY_A, TelemetryRequest(password="pw"))

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_400_for_non_repeater_contact(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)

        with patch("app.routers.contacts.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await request_telemetry(KEY_A, TelemetryRequest(password="pw"))

        assert exc.value.status_code == 400
        assert "not a repeater" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_status_retry_timeout_returns_504(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_status_sync = AsyncMock(side_effect=[None, None, None])

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(
                "app.routers.contacts.prepare_repeater_connection",
                new_callable=AsyncMock,
            ) as mock_prepare,
        ):
            with pytest.raises(HTTPException) as exc:
                await request_telemetry(KEY_A, TelemetryRequest(password="pw"))

        assert exc.value.status_code == 504
        assert mc.commands.req_status_sync.await_count == 3
        mock_prepare.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clock_timeout_uses_fallback_message_and_restores_auto_fetch(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.req_status_sync = AsyncMock(
            return_value={
                "pubkey_pre": "aaaaaaaaaaaa",
                "bat": 3775,
                "uptime": 1234,
            }
        )
        mc.commands.fetch_all_neighbours = AsyncMock(
            return_value={"neighbours": [{"pubkey": "abc123def456", "snr": 9.0, "secs_ago": 5}]}
        )
        mc.commands.req_acl_sync = AsyncMock(return_value=[{"key": "def456abc123", "perm": 2}])
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        # Clock fetch uses _fetch_repeater_response which calls get_msg() directly.
        # Return NO_MORE_MSGS to simulate no clock response.
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        # Clock is attempted twice, each with timeout=10.0. Provide enough ticks
        # for the deadline to expire on each attempt.
        clock_ticks = []
        for base in (0.0, 100.0):
            clock_ticks.extend([base, base + 5.0, base + 11.0])

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(
                "app.routers.contacts.prepare_repeater_connection",
                new_callable=AsyncMock,
            ) as mock_prepare,
            patch(_MONOTONIC, side_effect=clock_ticks),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await request_telemetry(KEY_A, TelemetryRequest(password="pw"))

        assert response.pubkey_prefix == "aaaaaaaaaaaa"
        assert response.battery_volts == 3.775
        assert response.clock_output is not None
        assert "unable to fetch `clock` output" in response.clock_output.lower()
        mock_prepare.assert_awaited_once()
        mc.stop_auto_message_fetching.assert_awaited_once()
        mc.start_auto_message_fetching.assert_awaited_once()


class TestRepeaterCommandRoute:
    @pytest.mark.asyncio
    async def test_send_cmd_error_raises_and_restores_auto_fetch(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "bad"})
        )

        with patch("app.routers.contacts.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert exc.value.status_code == 500
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_no_response_message(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(return_value=_radio_result(EventType.NO_MORE_MSGS))

        # Expire the deadline after a couple of ticks
        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=[0.0, 5.0, 25.0]),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert "no response" in response.response.lower()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_returns_command_response_text_and_sender_timestamp(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "firmware: v1.2.3",
                    "sender_timestamp": 1700000000,
                    "txt_type": 1,
                },
            )
        )

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "firmware: v1.2.3"
        assert response.sender_timestamp == 1700000000

    @pytest.mark.asyncio
    async def test_success_falls_back_to_legacy_timestamp_field(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {
                    "pubkey_prefix": KEY_A[:12],
                    "text": "firmware: v1.2.3",
                    "timestamp": 1700000000,
                    "txt_type": 1,
                },
            )
        )

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "firmware: v1.2.3"
        assert response.sender_timestamp == 1700000000

    @pytest.mark.asyncio
    async def test_unrelated_dm_during_command_does_not_prevent_success(self, test_db):
        """Unrelated DMs arriving during command wait are skipped; correct response returned."""
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        unrelated = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": "bbbbbbbbbbbb", "text": "hello from someone", "txt_type": 0},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "ver 1.0", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[unrelated, expected])

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "ver 1.0"

    @pytest.mark.asyncio
    async def test_channel_message_during_command_is_skipped(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        channel_msg = _radio_result(
            EventType.CHANNEL_MSG_RECV,
            {"channel_idx": 0, "text": "flood msg"},
        )
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "ok", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[channel_msg, expected])

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "ok"

    @pytest.mark.asyncio
    async def test_no_more_msgs_then_response_succeeds(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))

        no_msgs = _radio_result(EventType.NO_MORE_MSGS)
        expected = _radio_result(
            EventType.CONTACT_MSG_RECV,
            {"pubkey_prefix": KEY_A[:12], "text": "done", "txt_type": 1},
        )
        mc.commands.get_msg = AsyncMock(side_effect=[no_msgs, expected])

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(_MONOTONIC, side_effect=_advancing_clock()),
            patch("app.routers.contacts.asyncio.sleep", new_callable=AsyncMock),
        ):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "done"


class TestTraceRoute:
    @pytest.mark.asyncio
    async def test_send_trace_error_returns_500(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"err": "x"})
        )

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            with pytest.raises(HTTPException) as exc:
                await request_trace(KEY_A)

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_wait_timeout_returns_504(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.wait_for_event = AsyncMock(return_value=None)

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            with pytest.raises(HTTPException) as exc:
                await request_trace(KEY_A)

        assert exc.value.status_code == 504

    @pytest.mark.asyncio
    async def test_success_returns_remote_and_local_snr(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Client", contact_type=1)
        mc.commands.send_trace = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.wait_for_event = AsyncMock(
            return_value=MagicMock(payload={"path": [{"snr": 5.5}, {"snr": 3.2}], "path_len": 2})
        )

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch("app.routers.contacts.random.randint", return_value=1234),
        ):
            response = await request_trace(KEY_A)

        assert response.remote_snr == 5.5
        assert response.local_snr == 3.2
        assert response.path_len == 2
