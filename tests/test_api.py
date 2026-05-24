"""Tests for API endpoints.

These tests verify the REST API behavior for critical operations.
Uses httpx.AsyncClient or direct function calls with real in-memory SQLite.
"""

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.services.message_send as message_send_service
from app.radio import radio_manager
from app.repository import (
    ChannelRepository,
    ContactRepository,
    MessageRepository,
    RawPacketRepository,
)
from app.version_info import AppBuildInfo


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    prev_max_channels = radio_manager.max_channels
    prev_connection_info = radio_manager._connection_info
    prev_slot_by_key = radio_manager._channel_slot_by_key.copy()
    prev_key_by_slot = radio_manager._channel_key_by_slot.copy()
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock
    radio_manager.max_channels = prev_max_channels
    radio_manager._connection_info = prev_connection_info
    radio_manager._channel_slot_by_key = prev_slot_by_key
    radio_manager._channel_key_by_slot = prev_key_by_slot


@pytest.fixture(autouse=True)
def _disable_background_dm_retries(monkeypatch):
    monkeypatch.setattr(message_send_service, "DM_SEND_MAX_ATTEMPTS", 1)
    yield


def _patch_require_connected(mc=None, *, detail="Radio not connected"):
    if mc is None:
        return patch(
            "app.services.radio_runtime.radio_runtime.require_connected",
            side_effect=HTTPException(status_code=423, detail=detail),
        )
    return patch("app.services.radio_runtime.radio_runtime.require_connected", return_value=mc)


async def _insert_contact(public_key, name="Alice", **overrides):
    """Insert a contact into the test database."""
    data = {
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
    data.update(overrides)
    await ContactRepository.upsert(data)


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_connection_status(self):
        """Health endpoint returns radio connection status."""
        from fastapi.testclient import TestClient

        with patch("app.routers.health.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert data["radio_connected"] is True
            assert data["connection_info"] == "Serial: /dev/ttyUSB0"

    def test_health_disconnected_state(self):
        """Health endpoint reflects disconnected radio."""
        from fastapi.testclient import TestClient

        with patch("app.routers.health.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.connection_info = None
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = False
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert data["radio_connected"] is False
            assert data["connection_info"] is None

    def test_health_includes_radio_stats_when_available(self):
        """Health endpoint includes cached radio stats snapshot."""
        from fastapi.testclient import TestClient

        fake_stats = {
            "timestamp": 1700000000,
            "battery_mv": 4150,
            "uptime_secs": 3600,
            "noise_floor": -120,
            "last_rssi": -85,
            "last_snr": 9.5,
            "tx_air_secs": 100,
            "rx_air_secs": 200,
            "packets": {
                "recv": 500,
                "sent": 250,
                "flood_tx": 100,
                "direct_tx": 150,
                "flood_rx": 300,
                "direct_rx": 200,
            },
        }

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.get_latest_radio_stats", return_value=fake_stats),
        ):
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False

            from app.main import app

            client = TestClient(app)
            response = client.get("/api/health")

            assert response.status_code == 200
            stats = response.json()["radio_stats"]
            assert stats["battery_mv"] == 4150
            assert stats["uptime_secs"] == 3600
            assert stats["noise_floor"] == -120
            assert stats["packets_recv"] == 500
            assert stats["packets_sent"] == 250

    def test_health_radio_stats_null_when_no_data(self):
        """Health endpoint returns null radio_stats when cache is empty."""
        from fastapi.testclient import TestClient

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.get_latest_radio_stats", return_value={}),
        ):
            mock_rm.is_connected = False
            mock_rm.connection_info = None
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = False
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False

            from app.main import app

            client = TestClient(app)
            response = client.get("/api/health")

            assert response.status_code == 200
            assert response.json()["radio_stats"] is None


class TestDebugEndpoint:
    """Test the debug support snapshot endpoint."""

    def test_build_environment_exposes_env_settings(self):
        """_build_environment should expose env config without secrets."""
        from app.config import Settings
        from app.routers.debug import _build_environment

        with patch(
            "app.routers.debug.settings",
            Settings(
                serial_port="/dev/ttyUSB0",
                serial_baudrate=115200,
                log_level="DEBUG",
                database_path="data/test.db",
            ),
        ):
            env = _build_environment()

        assert env.connection_type == "serial"
        assert env.serial_port == "/dev/ttyUSB0"
        assert env.log_level == "DEBUG"
        assert env.database_path == "data/test.db"
        assert not hasattr(env, "ble_pin")
        assert not hasattr(env, "basic_auth_password")
        assert not hasattr(env, "basic_auth_username")

    def test_support_snapshot_sanitizes_radio_probe_location_fields(self):
        """Debug radio probe should redact advertised lat/lon from self_info."""
        from app.routers.debug import _sanitize_radio_probe_self_info

        sanitized = _sanitize_radio_probe_self_info(
            {
                "name": "FlightlessTestNode",
                "adv_lat": 47.786445,
                "adv_lon": -122.344011,
                "radio_freq": 910.525,
            }
        )

        assert sanitized == {
            "name": "FlightlessTestNode",
            "radio_freq": 910.525,
        }

    def test_support_snapshot_only_keeps_erroring_fanouts_in_health_summary(self):
        """Debug health summary should only include fanouts with non-empty last_error."""
        from app.routers.debug import _build_debug_health_summary
        from app.routers.health import FanoutStatusResponse

        summary = _build_debug_health_summary(
            {
                "database_size_mb": 1.23,
                "oldest_undecrypted_timestamp": 123,
                "fanout_statuses": {
                    "ok-id": {
                        "name": "OK Fanout",
                        "type": "bot",
                        "status": "connected",
                        "last_error": None,
                    },
                    "err-id": {
                        "name": "Broken Fanout",
                        "type": "mqtt_private",
                        "status": "error",
                        "last_error": "broker down",
                    },
                },
                "bots_disabled_source": None,
                "basic_auth_enabled": False,
            },
            radio_state="connected",
        )

        assert summary.fanouts_with_errors == {
            "err-id": FanoutStatusResponse(
                name="Broken Fanout",
                type="mqtt_private",
                status="error",
                last_error="broker down",
            )
        }

    @pytest.mark.asyncio
    async def test_support_snapshot_returns_runtime_when_disconnected(self, test_db, client):
        """Debug snapshot should still return logs and runtime state when radio is disconnected."""
        from app.config import clear_recent_log_lines
        from app.routers.debug import DebugApplicationInfo

        clear_recent_log_lines()
        radio_manager._meshcore = None
        radio_manager._connection_info = None

        with patch(
            "app.routers.debug._build_application_info",
            return_value=DebugApplicationInfo(
                version="3.2.0",
                version_source="pyproject",
                commit_hash="deadbeef",
                commit_source="git",
                git_branch="main",
                git_dirty=False,
                python_version="3.12.0",
            ),
        ):
            response = await client.get("/api/debug")

        assert response.status_code == 200
        payload = response.json()
        assert "app_info" not in payload["health"]
        assert "bots_disabled" not in payload["health"]
        assert "connection_info" not in payload["health"]
        assert "fanout_statuses" not in payload["health"]
        assert "radio_connected" not in payload["health"]
        assert "radio_device_info" not in payload["health"]
        assert "radio_initializing" not in payload["health"]
        assert "status" not in payload["health"]
        assert payload["health"]["fanouts_with_errors"] == {}
        assert payload["health"]["radio_state"] == "disconnected"
        assert payload["radio_probe"]["performed"] is False
        assert payload["radio_probe"]["errors"] == ["Radio not connected"]
        assert "multi_acks_enabled" not in payload["radio_probe"]
        assert "max_channels" not in payload["runtime"]
        assert "path_hash_mode" not in payload["runtime"]
        assert "environment" in payload
        assert payload["environment"]["connection_type"] in ("serial", "tcp", "ble")
        assert payload["runtime"]["channels_with_incoming_messages"] == 0
        assert payload["database"]["total_dms"] == 0
        assert payload["database"]["total_channel_messages"] == 0
        assert payload["database"]["total_outgoing"] == 0

    @pytest.mark.asyncio
    async def test_support_snapshot_includes_database_message_totals(self, test_db, client):
        """Debug snapshot includes stored DM/channel/outgoing totals."""
        await _insert_contact("ab" * 32, "Alice")
        await test_db.conn.execute(
            "INSERT INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
            ("CD" * 16, "#ops", 1, 0),
        )
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("PRIV", "ab" * 32, "hello", 1000, 0),
        )
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES (?, ?, ?, ?, ?)",
            ("CHAN", "CD" * 16, "room msg", 1001, 1),
        )
        await test_db.conn.commit()

        response = await client.get("/api/debug")

        assert response.status_code == 200
        payload = response.json()
        assert payload["database"]["total_dms"] == 1
        assert payload["database"]["total_channel_messages"] == 1
        assert payload["database"]["total_outgoing"] == 1

    @pytest.mark.asyncio
    async def test_support_snapshot_uses_lightweight_message_totals(self, test_db, client):
        """Debug snapshot should not call the full statistics aggregation."""
        with (
            patch(
                "app.routers.debug.StatisticsRepository.get_all",
                new=AsyncMock(side_effect=AssertionError("get_all should not be called")),
            ),
            patch(
                "app.routers.debug.StatisticsRepository.get_database_message_totals",
                new=AsyncMock(
                    return_value={
                        "total_dms": 0,
                        "total_channel_messages": 0,
                        "total_outgoing": 0,
                    }
                ),
            ),
        ):
            response = await client.get("/api/debug")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_support_snapshot_includes_persisted_app_settings(self, test_db, client):
        """Debug snapshot should expose the stored app settings row."""
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        response = await client.patch(
            "/api/settings",
            json={
                "max_radio_contacts": 321,
                "auto_decrypt_dm_on_advert": True,
                "advert_interval": 7200,
                "flood_scope": "US-CA",
                "blocked_keys": [pub_key],
                "blocked_names": ["Mallory"],
            },
        )
        assert response.status_code == 200

        response = await client.post(
            "/api/settings/favorites/toggle",
            json={"type": "contact", "id": pub_key},
        )
        assert response.status_code == 200

        response = await client.get("/api/debug")

        assert response.status_code == 200
        payload = response.json()
        assert payload["settings"]["max_radio_contacts"] == 321
        assert payload["settings"]["auto_decrypt_dm_on_advert"] is True
        assert payload["settings"]["advert_interval"] == 7200
        assert payload["settings"]["flood_scope"] == "#US-CA"
        assert payload["settings"]["blocked_keys_count"] == 1
        assert payload["settings"]["blocked_names_count"] == 1
        assert "favorites" not in payload["settings"]
        assert "blocked_keys" not in payload["settings"]
        assert "blocked_names" not in payload["settings"]
        assert "sidebar_sort_order" not in payload["settings"]


class TestRadioDisconnectedHandler:
    """Test that RadioDisconnectedError maps to 423."""

    @pytest.mark.asyncio
    async def test_disconnect_race_returns_423(self, test_db, client):
        """If radio disconnects between require_connected() and lock acquisition, return 423."""
        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        # require_connected() passes, but _meshcore is None when radio_operation() checks
        radio_manager._meshcore = None
        with _patch_require_connected(MagicMock()):
            response = await client.post(
                "/api/messages/direct", json={"destination": pub_key, "text": "Hi"}
            )

        assert response.status_code == 423
        assert "not connected" in response.json()["detail"].lower()


class TestDebugApplicationInfo:
    """Test debug application metadata resolution."""

    def test_build_application_info_uses_release_build_info_without_git(self, tmp_path):
        """Release bundles should still surface commit metadata without a .git directory."""
        from app.routers import debug as debug_router

        with (
            patch(
                "app.routers.debug.get_app_build_info",
                return_value=AppBuildInfo(
                    version="3.4.0",
                    version_source="pyproject",
                    commit_hash="cf1a55e2",
                    commit_source="build_info",
                ),
            ),
            patch("app.routers.debug.git_output", return_value=None),
        ):
            info = debug_router._build_application_info()

        assert info.version == "3.4.0"
        assert info.version_source == "pyproject"
        assert info.commit_hash == "cf1a55e2"
        assert info.commit_source == "build_info"
        assert info.git_branch is None
        assert info.git_dirty is False

    def test_build_application_info_ignores_invalid_release_build_info(self, tmp_path):
        """Malformed release metadata should not break the debug endpoint."""
        from app.routers import debug as debug_router

        with (
            patch(
                "app.routers.debug.get_app_build_info",
                return_value=AppBuildInfo(
                    version="3.4.0",
                    version_source="pyproject",
                    commit_hash=None,
                    commit_source=None,
                ),
            ),
            patch("app.routers.debug.git_output", return_value=None),
        ):
            info = debug_router._build_application_info()

        assert info.version == "3.4.0"
        assert info.version_source == "pyproject"
        assert info.commit_hash is None
        assert info.commit_source is None
        assert info.git_branch is None
        assert info.git_dirty is False


class TestMessagesEndpoint:
    """Test message-related endpoints."""

    @pytest.mark.asyncio
    async def test_send_direct_message_requires_connection(self, test_db, client):
        """Sending message when disconnected returns 423."""
        with _patch_require_connected():
            response = await client.post(
                "/api/messages/direct", json={"destination": "abc123", "text": "Hello"}
            )

            assert response.status_code == 423
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_send_channel_message_requires_connection(self, test_db, client):
        """Sending channel message when disconnected returns 423."""
        with _patch_require_connected():
            response = await client.post(
                "/api/messages/channel",
                json={"channel_key": "0123456789ABCDEF0123456789ABCDEF", "text": "Hello"},
            )

            assert response.status_code == 423

    @pytest.mark.asyncio
    async def test_send_direct_message_emits_websocket_message_event(self, test_db, client):
        """POST /messages/direct should emit a WS message event for other clients."""
        from meshcore import EventType

        pub_key = "ab" * 32
        await _insert_contact(pub_key, "Alice")

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix.return_value = {"public_key": pub_key}
        mock_mc.commands.add_contact = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={})
        )
        mock_mc.commands.send_msg = AsyncMock(
            return_value=MagicMock(type=EventType.MSG_SENT, payload={})
        )

        radio_manager._meshcore = mock_mc
        with (
            _patch_require_connected(mock_mc),
            patch("app.routers.messages.broadcast_event") as mock_broadcast,
        ):
            response = await client.post(
                "/api/messages/direct",
                json={"destination": pub_key, "text": "Hello"},
            )

            assert response.status_code == 200
            mock_broadcast.assert_called_once()
            event_type, payload = mock_broadcast.call_args.args
            assert event_type == "message"
            assert payload["type"] == "PRIV"

            # Verify message was stored in real DB
            messages = await MessageRepository.get_all(conversation_key=pub_key)
            assert len(messages) == 1
            assert messages[0].text == "Hello"

    @pytest.mark.asyncio
    async def test_send_channel_message_emits_websocket_message_event(self, test_db, client):
        """POST /messages/channel should emit a WS message event for other clients."""
        from meshcore import EventType

        chan_key = "AA" * 16
        await ChannelRepository.upsert(key=chan_key, name="Public")

        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "TestNode"}
        ok_result = MagicMock(type=EventType.MSG_SENT, payload={})
        mock_mc.commands.set_channel = AsyncMock(return_value=ok_result)
        mock_mc.commands.send_chan_msg = AsyncMock(return_value=ok_result)

        radio_manager._meshcore = mock_mc
        with (
            _patch_require_connected(mock_mc),
            patch("app.routers.messages.broadcast_event") as mock_broadcast,
        ):
            response = await client.post(
                "/api/messages/channel",
                json={"channel_key": chan_key, "text": "Hello room"},
            )

            assert response.status_code == 200
            mock_broadcast.assert_called_once()
            event_type, payload = mock_broadcast.call_args.args
            assert event_type == "message"
            assert payload["type"] == "CHAN"

    @pytest.mark.asyncio
    async def test_send_direct_message_contact_not_found(self, test_db, client):
        """Sending to unknown contact returns 404."""
        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix.return_value = None

        with _patch_require_connected(mock_mc):
            response = await client.post(
                "/api/messages/direct", json={"destination": "nonexistent", "text": "Hello"}
            )

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_send_direct_message_duplicate_returns_422(self, test_db):
        """If MessageRepository.create returns None (duplicate), returns 422."""
        from app.models import SendDirectMessageRequest
        from app.routers.messages import send_direct_message

        pub_key = "a" * 64
        await _insert_contact(pub_key, "TestContact")

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix.return_value = {"public_key": pub_key}
        mock_mc.commands.add_contact = AsyncMock(
            return_value=MagicMock(type=MagicMock(name="OK"), payload={})
        )
        mock_mc.commands.send_msg = AsyncMock(
            return_value=MagicMock(type=MagicMock(name="OK"), payload={"expected_ack": b"\x00\x01"})
        )

        radio_manager._meshcore = mock_mc
        with (
            _patch_require_connected(mock_mc),
            patch("app.routers.messages.MessageRepository") as mock_msg_repo,
        ):
            mock_msg_repo.get_by_content = AsyncMock(return_value=None)
            # Simulate duplicate - create returns None
            mock_msg_repo.create = AsyncMock(return_value=None)

            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await send_direct_message(
                    SendDirectMessageRequest(destination=pub_key, text="Hello")
                )

            assert exc_info.value.status_code == 422
            assert "unexpected duplicate" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_send_channel_message_duplicate_returns_422(self, test_db):
        """If MessageRepository.create returns None (duplicate), returns 422."""
        from app.models import SendChannelMessageRequest
        from app.routers.messages import send_channel_message

        chan_key = "0123456789ABCDEF0123456789ABCDEF"
        await ChannelRepository.upsert(key=chan_key, name="test")

        mock_mc = MagicMock()
        mock_mc.commands.send_chan_msg = AsyncMock(
            return_value=MagicMock(type=MagicMock(name="OK"), payload={})
        )
        mock_mc.commands.set_channel = AsyncMock(
            return_value=MagicMock(type=MagicMock(name="OK"), payload={})
        )

        radio_manager._meshcore = mock_mc
        with (
            _patch_require_connected(mock_mc),
            patch("app.routers.messages.MessageRepository") as mock_msg_repo,
        ):
            mock_msg_repo.get_by_content = AsyncMock(return_value=None)
            # Simulate duplicate - create returns None
            mock_msg_repo.create = AsyncMock(return_value=None)

            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="Hello")
                )

            assert exc_info.value.status_code == 422
            assert "unexpected duplicate" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_resend_channel_message_requires_connection(self, test_db, client):
        """Resend endpoint returns 423 when radio is disconnected."""
        with _patch_require_connected():
            response = await client.post("/api/messages/channel/1/resend")

            assert response.status_code == 423
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_resend_channel_message_success(self, test_db, client):
        """Resend endpoint reuses timestamp bytes and strips sender prefix."""
        from meshcore import EventType

        chan_key = "AB" * 16
        await ChannelRepository.upsert(key=chan_key, name="#resend")
        sent_at = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="TestNode: hello world",
            conversation_key=chan_key,
            sender_timestamp=sent_at,
            received_at=sent_at,
            outgoing=True,
        )
        assert msg_id is not None

        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "TestNode"}
        mock_mc.commands = MagicMock()
        mock_mc.commands.set_channel = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={})
        )
        mock_mc.commands.send_chan_msg = AsyncMock(
            return_value=MagicMock(type=EventType.MSG_SENT, payload={})
        )

        radio_manager._meshcore = mock_mc
        with _patch_require_connected(mock_mc):
            response = await client.post(f"/api/messages/channel/{msg_id}/resend")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "message_id": msg_id}

        set_kwargs = mock_mc.commands.set_channel.await_args.kwargs
        assert set_kwargs["channel_idx"] == 0
        assert set_kwargs["channel_name"] == "#resend"
        assert set_kwargs["channel_secret"] == bytes.fromhex(chan_key)

        send_kwargs = mock_mc.commands.send_chan_msg.await_args.kwargs
        assert send_kwargs["chan"] == 0
        assert send_kwargs["msg"] == "hello world"
        assert send_kwargs["timestamp"] == sent_at.to_bytes(4, "little")

    @pytest.mark.asyncio
    async def test_resend_channel_message_new_timestamp_returns_message_payload(
        self, test_db, client
    ):
        """New-timestamp resend returns the created message payload for local UI append."""
        from meshcore import EventType

        chan_key = "EF" * 16
        await ChannelRepository.upsert(key=chan_key, name="#resend-new")
        sent_at = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="TestNode: hello again",
            conversation_key=chan_key,
            sender_timestamp=sent_at,
            received_at=sent_at,
            outgoing=True,
        )
        assert msg_id is not None

        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "TestNode", "public_key": "ab" * 32}
        mock_mc.commands = MagicMock()
        mock_mc.commands.set_channel = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={})
        )
        mock_mc.commands.send_chan_msg = AsyncMock(
            return_value=MagicMock(type=EventType.MSG_SENT, payload={})
        )

        radio_manager._meshcore = mock_mc
        with _patch_require_connected(mock_mc):
            response = await client.post(
                f"/api/messages/channel/{msg_id}/resend?new_timestamp=true"
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["message_id"] != msg_id
        assert payload["message"]["id"] == payload["message_id"]
        assert payload["message"]["conversation_key"] == chan_key
        assert payload["message"]["outgoing"] is True

    @pytest.mark.asyncio
    async def test_resend_channel_message_window_expired(self, test_db, client):
        """Resend endpoint rejects channel messages older than 30 seconds."""
        chan_key = "CD" * 16
        await ChannelRepository.upsert(key=chan_key, name="#old")
        sent_at = int(time.time()) - 60
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="TestNode: too old",
            conversation_key=chan_key,
            sender_timestamp=sent_at,
            received_at=sent_at,
            outgoing=True,
        )
        assert msg_id is not None

        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "TestNode"}
        mock_mc.commands = MagicMock()
        mock_mc.commands.set_channel = AsyncMock()
        mock_mc.commands.send_chan_msg = AsyncMock()

        with _patch_require_connected(mock_mc):
            response = await client.post(f"/api/messages/channel/{msg_id}/resend")

        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower()
        assert mock_mc.commands.set_channel.await_count == 0
        assert mock_mc.commands.send_chan_msg.await_count == 0

    @pytest.mark.asyncio
    async def test_resend_channel_message_returns_404_for_missing(self, test_db, client):
        """Resend endpoint returns 404 for nonexistent message ID."""
        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "TestNode"}
        mock_mc.commands = MagicMock()
        mock_mc.commands.set_channel = AsyncMock()
        mock_mc.commands.send_chan_msg = AsyncMock()

        with _patch_require_connected(mock_mc):
            response = await client.post("/api/messages/channel/999999/resend")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
        assert mock_mc.commands.set_channel.await_count == 0
        assert mock_mc.commands.send_chan_msg.await_count == 0


class TestChannelsEndpoint:
    """Test channel-related endpoints."""

    @pytest.mark.asyncio
    async def test_create_hashtag_channel_derives_key(self, test_db):
        """Creating hashtag channel derives key from name and stores in DB."""
        from app.routers.channels import CreateChannelRequest, create_channel

        request = CreateChannelRequest(name="#mychannel")
        result = await create_channel(request)

        # Verify the key derivation
        expected_key_hex = hashlib.sha256(b"#mychannel").digest()[:16].hex().upper()
        assert result.key == expected_key_hex
        assert result.name == "#mychannel"

        # Verify stored in real DB
        channel = await ChannelRepository.get_by_key(expected_key_hex)
        assert channel is not None
        assert channel.name == "#mychannel"
        assert channel.is_hashtag is True
        assert channel.on_radio is False

    @pytest.mark.asyncio
    async def test_create_channel_with_explicit_key(self, test_db):
        """Creating channel with explicit key uses provided key."""
        from app.routers.channels import CreateChannelRequest, create_channel

        explicit_key = "0123456789abcdef0123456789abcdef"  # 32 hex chars = 16 bytes
        request = CreateChannelRequest(name="private", key=explicit_key)
        result = await create_channel(request)

        assert result.key == explicit_key.upper()

        # Verify stored in real DB
        channel = await ChannelRepository.get_by_key(explicit_key.upper())
        assert channel is not None
        assert channel.name == "private"
        assert channel.on_radio is False


class TestPacketsEndpoint:
    """Test packet decryption endpoints."""

    def test_get_undecrypted_count(self):
        """Get undecrypted packet count returns correct value."""
        from fastapi.testclient import TestClient

        with patch("app.routers.packets.RawPacketRepository") as mock_repo:
            mock_repo.get_undecrypted_count = AsyncMock(return_value=42)

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/packets/undecrypted/count")

            assert response.status_code == 200
            assert response.json()["count"] == 42


class TestReadStateEndpoints:
    """Test read state tracking endpoints."""

    @pytest.mark.asyncio
    async def test_mark_contact_read_updates_timestamp(self, test_db):
        """Marking contact as read updates last_read_at in database."""
        pub_key = "abc123def456789012345678901234567890123456789012345678901234"
        await _insert_contact(pub_key, "TestContact")

        before_time = int(time.time())

        updated = await ContactRepository.update_last_read_at(pub_key)
        assert updated is True

        contact = await ContactRepository.get_by_key(pub_key)
        assert contact is not None
        assert contact.last_read_at is not None
        assert contact.last_read_at >= before_time

    @pytest.mark.asyncio
    async def test_mark_channel_read_updates_timestamp(self, test_db):
        """Marking channel as read updates last_read_at in database."""
        chan_key = "0123456789ABCDEF0123456789ABCDEF"
        await ChannelRepository.upsert(key=chan_key, name="#testchannel")

        before_time = int(time.time())

        updated = await ChannelRepository.update_last_read_at(chan_key)
        assert updated is True

        channel = await ChannelRepository.get_by_key(chan_key)
        assert channel is not None
        assert channel.last_read_at is not None
        assert channel.last_read_at >= before_time

    @pytest.mark.asyncio
    async def test_mark_nonexistent_contact_returns_false(self, test_db):
        """Marking nonexistent contact returns False."""
        updated = await ContactRepository.update_last_read_at("nonexistent")
        assert updated is False

    @pytest.mark.asyncio
    async def test_mark_contact_read_endpoint_returns_404_for_missing(self, test_db, client):
        """Mark-read endpoint returns 404 for nonexistent contact."""
        response = await client.post("/api/contacts/nonexistent/mark-read")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_mark_channel_read_endpoint_returns_404_for_missing(self, test_db, client):
        """Mark-read endpoint returns 404 for nonexistent channel."""
        response = await client.post("/api/channels/NONEXISTENT/mark-read")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_unreads_returns_counts_and_mentions(self, test_db):
        """GET /unreads returns unread counts, mentions, and last message times."""
        chan_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
        contact_key = "abcd" * 16

        await ChannelRepository.upsert(key=chan_key, name="Public")
        await ChannelRepository.update_last_read_at(chan_key, 1000)
        await _insert_contact(contact_key, "Alice")
        await ContactRepository.update_last_read_at(contact_key, 1000)

        # 2 unread channel msgs (received_at > last_read_at=1000), 1 read, 1 outgoing
        await MessageRepository.create(
            msg_type="CHAN",
            text="Bob: hello",
            received_at=1001,
            conversation_key=chan_key,
            sender_timestamp=1001,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Bob: @[testuser] hey",
            received_at=1002,
            conversation_key=chan_key,
            sender_timestamp=1002,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Bob: old msg",
            received_at=999,
            conversation_key=chan_key,
            sender_timestamp=999,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Me: outgoing",
            received_at=1003,
            conversation_key=chan_key,
            sender_timestamp=1003,
            outgoing=True,
        )
        # 1 unread DM with mention
        await MessageRepository.create(
            msg_type="PRIV",
            text="hi @[TeStUsEr] there",
            received_at=1005,
            conversation_key=contact_key,
            sender_timestamp=1005,
        )

        result = await MessageRepository.get_unread_counts("TestUser")

        # Channel: 2 unread (1001 and 1002), one has mention
        assert result["counts"][f"channel-{chan_key}"] == 2
        assert result["mentions"][f"channel-{chan_key}"] is True

        # Contact: 1 unread with mention (case-insensitive)
        assert result["counts"][f"contact-{contact_key}"] == 1
        assert result["mentions"][f"contact-{contact_key}"] is True

        # Last message times should include all conversations
        assert result["last_message_times"][f"channel-{chan_key}"] == 1003
        assert result["last_message_times"][f"contact-{contact_key}"] == 1005
        assert result["last_read_ats"][f"channel-{chan_key}"] == 1000
        assert result["last_read_ats"][f"contact-{contact_key}"] == 1000

    @pytest.mark.asyncio
    async def test_get_unreads_no_name_skips_mentions(self, test_db):
        """Unreads without a radio name returns counts but no mention flags."""
        chan_key = "CHAN1KEY1CHAN1KEY1CHAN1KEY1CHAN1KEY1"
        await ChannelRepository.upsert(key=chan_key, name="Public")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="CHAN",
            text="Bob: @[Alice] hey",
            received_at=1001,
            conversation_key=chan_key,
            sender_timestamp=1001,
        )

        result = await MessageRepository.get_unread_counts(None)

        assert result["counts"][f"channel-{chan_key}"] == 1
        assert len(result["mentions"]) == 0

    @pytest.mark.asyncio
    async def test_unreads_endpoint_sources_name_from_radio(self, test_db, client):
        """GET /unreads sources the user's name from the radio for mention detection."""
        chan_key = "MENTIONENDPOINT1MENTIONENDPOINT1"
        await ChannelRepository.upsert(key=chan_key, name="Public")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="CHAN",
            text="hey @[RadioUser] check this",
            received_at=1001,
            conversation_key=chan_key,
            sender_timestamp=1001,
        )

        # Mock radio_manager.meshcore to return a name
        mock_mc = MagicMock()
        mock_mc.self_info = {"name": "RadioUser"}
        with patch("app.routers.read_state.radio_manager") as mock_rm:
            mock_rm.meshcore = mock_mc
            response = await client.get("/api/read-state/unreads")

        assert response.status_code == 200
        data = response.json()
        assert data["counts"][f"channel-{chan_key}"] == 1
        assert data["mentions"][f"channel-{chan_key}"] is True
        assert data["last_read_ats"][f"channel-{chan_key}"] == 0

    @pytest.mark.asyncio
    async def test_unreads_endpoint_no_radio_skips_mentions(self, test_db, client):
        """GET /unreads with no radio connected still returns counts without mentions."""
        chan_key = "NORADIOENDPOINT1NORADIOENDPOINT1"
        await ChannelRepository.upsert(key=chan_key, name="Public")
        await ChannelRepository.update_last_read_at(chan_key, 0)

        await MessageRepository.create(
            msg_type="CHAN",
            text="hey @[Someone] check this",
            received_at=1001,
            conversation_key=chan_key,
            sender_timestamp=1001,
        )

        # Mock radio_manager.meshcore as None (disconnected)
        with patch("app.routers.read_state.radio_manager") as mock_rm:
            mock_rm.meshcore = None
            response = await client.get("/api/read-state/unreads")

        assert response.status_code == 200
        data = response.json()
        assert data["counts"][f"channel-{chan_key}"] == 1
        assert len(data["mentions"]) == 0
        assert data["last_read_ats"][f"channel-{chan_key}"] == 0

    @pytest.mark.asyncio
    async def test_unreads_reset_after_mark_read(self, test_db):
        """Marking a conversation as read zeroes its unread count; new messages after count again."""
        chan_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"
        await ChannelRepository.upsert(key=chan_key, name="Public")
        await ChannelRepository.update_last_read_at(chan_key, 1000)

        # 2 unread messages (received_at > last_read_at=1000)
        await MessageRepository.create(
            msg_type="CHAN",
            text="msg1",
            received_at=1001,
            conversation_key=chan_key,
            sender_timestamp=1001,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="msg2",
            received_at=1002,
            conversation_key=chan_key,
            sender_timestamp=1002,
        )

        # Verify 2 unread
        result = await MessageRepository.get_unread_counts(None)
        assert result["counts"][f"channel-{chan_key}"] == 2

        # Mark as read
        await ChannelRepository.update_last_read_at(chan_key, 1002)

        # Verify 0 unread
        result = await MessageRepository.get_unread_counts(None)
        assert result["counts"].get(f"channel-{chan_key}", 0) == 0

        # New message arrives after the read point
        await MessageRepository.create(
            msg_type="CHAN",
            text="msg3",
            received_at=1003,
            conversation_key=chan_key,
            sender_timestamp=1003,
        )

        # Verify exactly 1 unread
        result = await MessageRepository.get_unread_counts(None)
        assert result["counts"][f"channel-{chan_key}"] == 1

    @pytest.mark.asyncio
    async def test_unreads_exclude_outgoing_messages(self, test_db):
        """Outgoing messages should never count as unread."""
        contact_key = "abcd" * 16
        await _insert_contact(contact_key, "Bob")
        await ContactRepository.update_last_read_at(contact_key, 1000)

        # 1 incoming (should count) + 2 outgoing (should NOT count)
        await MessageRepository.create(
            msg_type="PRIV",
            text="incoming msg",
            received_at=1001,
            conversation_key=contact_key,
            sender_timestamp=1001,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="my reply",
            received_at=1002,
            conversation_key=contact_key,
            sender_timestamp=1002,
            outgoing=True,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="another reply",
            received_at=1003,
            conversation_key=contact_key,
            sender_timestamp=1003,
            outgoing=True,
        )

        result = await MessageRepository.get_unread_counts(None)
        # Only the 1 incoming message should count as unread
        assert result["counts"][f"contact-{contact_key}"] == 1

    @pytest.mark.asyncio
    async def test_mark_all_read_updates_all_conversations(self, test_db):
        """Bulk mark-all-read updates all contacts and channels."""
        await _insert_contact("contact1", "Alice")
        await _insert_contact("contact2", "Bob")
        await ChannelRepository.upsert(key="CHAN1KEY1CHAN1KEY1CHAN1KEY1CHAN1KEY1", name="#test1")
        await ChannelRepository.upsert(key="CHAN2KEY2CHAN2KEY2CHAN2KEY2CHAN2KEY2", name="#test2")

        before_time = int(time.time())

        from app.routers.read_state import mark_all_read

        result = await mark_all_read()

        assert result["status"] == "ok"
        assert result["timestamp"] >= before_time

        # Verify all contacts updated
        for key in ["contact1", "contact2"]:
            contact = await ContactRepository.get_by_key(key)
            assert contact.last_read_at >= before_time

        # Verify all channels updated
        for key in ["CHAN1KEY1CHAN1KEY1CHAN1KEY1CHAN1KEY1", "CHAN2KEY2CHAN2KEY2CHAN2KEY2CHAN2KEY2"]:
            channel = await ChannelRepository.get_by_key(key)
            assert channel.last_read_at >= before_time


class TestRawPacketRepository:
    """Test raw packet storage with deduplication."""

    @pytest.mark.asyncio
    async def test_create_returns_id_for_new_packet(self, test_db):
        """First insert of packet data returns a valid ID."""
        packet_data = b"\x01\x02\x03\x04\x05"
        packet_id, is_new = await RawPacketRepository.create(packet_data, 1234567890)

        assert packet_id is not None
        assert packet_id > 0
        assert is_new is True

    @pytest.mark.asyncio
    async def test_different_packets_both_stored(self, test_db):
        """Different packet data both get stored with unique IDs."""
        packet1 = b"\x01\x02\x03"
        packet2 = b"\x04\x05\x06"

        id1, is_new1 = await RawPacketRepository.create(packet1, 1234567890)
        id2, is_new2 = await RawPacketRepository.create(packet2, 1234567891)

        assert id1 is not None
        assert id2 is not None
        assert id1 != id2
        assert is_new1 is True
        assert is_new2 is True

    @pytest.mark.asyncio
    async def test_duplicate_packet_returns_existing_id(self, test_db):
        """Inserting same payload twice returns existing ID and is_new=False."""
        # Same packet data inserted twice
        packet_data = b"\x01\x02\x03\x04\x05"
        id1, is_new1 = await RawPacketRepository.create(packet_data, 1234567890)
        id2, is_new2 = await RawPacketRepository.create(packet_data, 1234567891)

        # Both should return the same ID
        assert id1 == id2
        # First is new, second is not
        assert is_new1 is True
        assert is_new2 is False

    @pytest.mark.asyncio
    async def test_malformed_packet_uses_full_data_hash(self, test_db):
        """Malformed packets (can't extract payload) hash full data for dedup."""
        # Single byte is too short to be valid packet (extract_payload returns None)
        malformed = b"\x01"
        id1, is_new1 = await RawPacketRepository.create(malformed, 1234567890)
        id2, is_new2 = await RawPacketRepository.create(malformed, 1234567891)

        # Should still deduplicate using full data hash
        assert id1 == id2
        assert is_new1 is True
        assert is_new2 is False

        # Different malformed packet should get different ID
        different_malformed = b"\x02"
        id3, is_new3 = await RawPacketRepository.create(different_malformed, 1234567892)
        assert id3 != id1
        assert is_new3 is True

    @pytest.mark.asyncio
    async def test_prune_old_undecrypted_deletes_old_packets(self, test_db):
        """Prune deletes undecrypted packets older than specified days."""
        now = int(time.time())
        old_timestamp = now - (15 * 86400)  # 15 days ago
        recent_timestamp = now - (5 * 86400)  # 5 days ago

        # Insert old undecrypted packet
        await RawPacketRepository.create(b"\x01\x02\x03", old_timestamp)
        # Insert recent undecrypted packet
        await RawPacketRepository.create(b"\x04\x05\x06", recent_timestamp)
        # Insert old but decrypted packet (should NOT be deleted)
        old_id, _ = await RawPacketRepository.create(b"\x07\x08\x09", old_timestamp)
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="test_key",
            text="test",
            sender_timestamp=old_timestamp,
            received_at=old_timestamp,
        )
        await RawPacketRepository.mark_decrypted(old_id, msg_id)

        # Prune packets older than 10 days
        deleted = await RawPacketRepository.prune_old_undecrypted(10)

        assert deleted == 1  # Only the old undecrypted packet

    @pytest.mark.asyncio
    async def test_prune_old_undecrypted_returns_zero_when_nothing_to_delete(self, test_db):
        """Prune returns 0 when no packets match criteria."""
        now = int(time.time())
        recent_timestamp = now - (5 * 86400)  # 5 days ago

        # Insert only recent packet
        await RawPacketRepository.create(b"\x01\x02\x03", recent_timestamp)

        # Prune packets older than 10 days (none should match)
        deleted = await RawPacketRepository.prune_old_undecrypted(10)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_purge_linked_to_messages_deletes_only_linked_packets(self, test_db):
        """Purge linked raw packets removes only rows with a message_id."""
        ts = int(time.time())
        msg_id_1 = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="k1",
            text="t1",
            sender_timestamp=ts,
            received_at=ts,
        )
        msg_id_2 = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="k2",
            text="t2",
            sender_timestamp=ts,
            received_at=ts,
        )
        linked_1, _ = await RawPacketRepository.create(b"\x01\x02\x03", ts)
        linked_2, _ = await RawPacketRepository.create(b"\x04\x05\x06", ts)
        await RawPacketRepository.mark_decrypted(linked_1, msg_id_1)
        await RawPacketRepository.mark_decrypted(linked_2, msg_id_2)

        await RawPacketRepository.create(b"\x07\x08\x09", ts)  # undecrypted, should remain

        deleted = await RawPacketRepository.purge_linked_to_messages()
        assert deleted == 2

        remaining = await RawPacketRepository.get_undecrypted_count()
        assert remaining == 1


class TestMaintenanceEndpoint:
    """Test database maintenance endpoint."""

    @pytest.mark.asyncio
    async def test_maintenance_prunes_and_vacuums(self, test_db):
        """Maintenance endpoint prunes old packets and runs vacuum."""
        from app.routers.packets import MaintenanceRequest, run_maintenance

        now = int(time.time())
        old_timestamp = now - (20 * 86400)  # 20 days ago

        # Insert old undecrypted packets
        await RawPacketRepository.create(b"\x01\x02\x03", old_timestamp)
        await RawPacketRepository.create(b"\x04\x05\x06", old_timestamp)

        request = MaintenanceRequest(prune_undecrypted_days=14)
        result = await run_maintenance(request)

        assert result.packets_deleted == 2
        assert result.vacuumed is True

    @pytest.mark.asyncio
    async def test_maintenance_can_purge_linked_raw_packets(self, test_db):
        """Maintenance endpoint can purge raw packets linked to messages."""
        from app.routers.packets import MaintenanceRequest, run_maintenance

        ts = int(time.time())
        msg_id_1 = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="k1",
            text="t1",
            sender_timestamp=ts,
            received_at=ts,
        )
        msg_id_2 = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="k2",
            text="t2",
            sender_timestamp=ts,
            received_at=ts,
        )
        linked_1, _ = await RawPacketRepository.create(b"\x0a\x0b\x0c", ts)
        linked_2, _ = await RawPacketRepository.create(b"\x0d\x0e\x0f", ts)
        await RawPacketRepository.mark_decrypted(linked_1, msg_id_1)
        await RawPacketRepository.mark_decrypted(linked_2, msg_id_2)

        request = MaintenanceRequest(purge_linked_raw_packets=True)
        result = await run_maintenance(request)

        assert result.packets_deleted == 2
        assert result.vacuumed is True


class TestHealthEndpointDatabaseSize:
    """Test database size reporting in health endpoint."""

    def test_health_includes_database_size(self):
        """Health endpoint includes database_size_mb field."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.os.path.getsize") as mock_getsize,
        ):
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False
            mock_getsize.return_value = 10 * 1024 * 1024  # 10 MB

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert "database_size_mb" in data
            assert data["database_size_mb"] == 10.0


class TestHealthEndpointOldestUndecrypted:
    """Test oldest undecrypted packet timestamp in health endpoint."""

    def test_health_includes_oldest_undecrypted_timestamp(self):
        """Health endpoint includes oldest_undecrypted_timestamp when packets exist."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.os.path.getsize") as mock_getsize,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
        ):
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False
            mock_getsize.return_value = 5 * 1024 * 1024  # 5 MB
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=1700000000)

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert "oldest_undecrypted_timestamp" in data
            assert data["oldest_undecrypted_timestamp"] == 1700000000

    def test_health_oldest_undecrypted_null_when_none(self):
        """Health endpoint returns null for oldest_undecrypted_timestamp when no packets."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.os.path.getsize") as mock_getsize,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
        ):
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False
            mock_getsize.return_value = 1 * 1024 * 1024  # 1 MB
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert "oldest_undecrypted_timestamp" in data
            assert data["oldest_undecrypted_timestamp"] is None

    def test_health_handles_db_not_connected(self):
        """Health endpoint gracefully handles database not connected."""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        with (
            patch("app.routers.health.radio_manager") as mock_rm,
            patch("app.routers.health.os.path.getsize") as mock_getsize,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
        ):
            mock_rm.is_connected = False
            mock_rm.connection_info = None
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = False
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = False
            mock_getsize.side_effect = OSError("File not found")
            mock_repo.get_oldest_undecrypted = AsyncMock(side_effect=RuntimeError("No DB"))

            from app.main import app

            client = TestClient(app)

            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()
            assert data["oldest_undecrypted_timestamp"] is None
            assert data["database_size_mb"] == 0.0
