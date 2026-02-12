"""Tests for repeater-specific contacts routes (telemetry, command, trace)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.database import Database
from app.models import CommandRequest, TelemetryRequest
from app.repository import ContactRepository
from app.routers.contacts import request_telemetry, request_trace, send_repeater_command

KEY_A = "aa" * 32


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
        mc.wait_for_event = AsyncMock(side_effect=[None, None])  # two clock attempts, no response

        with (
            patch("app.routers.contacts.require_connected", return_value=mc),
            patch(
                "app.routers.contacts.prepare_repeater_connection",
                new_callable=AsyncMock,
            ) as mock_prepare,
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
        mc.wait_for_event = AsyncMock(return_value=None)

        with patch("app.routers.contacts.require_connected", return_value=mc):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert "no response" in response.response.lower()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_returns_command_response_text_and_timestamp(self, test_db):
        mc = _mock_mc()
        await _insert_contact(KEY_A, name="Repeater", contact_type=2)
        mc.commands.send_cmd = AsyncMock(return_value=_radio_result(EventType.OK))
        mc.wait_for_event = AsyncMock(return_value=MagicMock())
        mc.commands.get_msg = AsyncMock(
            return_value=_radio_result(
                EventType.CONTACT_MSG_RECV,
                {"text": "firmware: v1.2.3", "timestamp": 1700000000},
            )
        )

        with patch("app.routers.contacts.require_connected", return_value=mc):
            response = await send_repeater_command(KEY_A, CommandRequest(command="ver"))

        assert response.command == "ver"
        assert response.response == "firmware: v1.2.3"
        assert response.sender_timestamp == 1700000000


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
