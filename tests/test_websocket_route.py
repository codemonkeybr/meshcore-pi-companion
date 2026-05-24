"""Tests for the WebSocket route endpoint (/api/ws).

These integration tests verify the WebSocket endpoint behavior:
- Initial health message sent on connect
- Ping/pong keepalive mechanism
- Clean disconnect handling

Uses FastAPI's TestClient synchronous WebSocket support with mocked
radio_manager and health data dependencies.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.websocket import ws_manager


@pytest.fixture(autouse=True)
def _clean_ws_manager():
    """Ensure ws_manager has no stale connections between tests."""
    ws_manager.active_connections.clear()
    yield
    ws_manager.active_connections.clear()


class TestWebSocketEndpoint:
    """Tests for the /api/ws WebSocket endpoint."""

    def test_receives_initial_health_on_connect(self):
        """Client receives a health event with radio status immediately after connecting."""
        with (
            patch("app.routers.ws.radio_manager") as mock_ws_rm,
            patch("app.routers.health.radio_manager") as mock_health_rm,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.health.os.path.getsize", return_value=1024 * 1024),
        ):
            mock_ws_rm.is_connected = True
            mock_ws_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_connected = True
            mock_health_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_setup_in_progress = False
            mock_health_rm.is_setup_complete = True
            mock_health_rm.connection_desired = True
            mock_health_rm.is_reconnecting = False
            mock_health_rm.device_info_loaded = False
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
            mock_settings.database_path = "/tmp/test.db"
            mock_settings.disable_bots = False

            from app.main import app

            client = TestClient(app)

            with client.websocket_connect("/api/ws") as ws:
                data = ws.receive_json()

                assert data["type"] == "health"
                assert "data" in data

                health = data["data"]
                assert health["radio_connected"] is True
                assert health["connection_info"] == "Serial: /dev/ttyUSB0"
                assert health["status"] == "ok"
                assert "database_size_mb" in health
                assert "oldest_undecrypted_timestamp" in health

    def test_initial_health_reflects_disconnected_radio(self):
        """Health event reflects degraded status when radio is not connected."""
        with (
            patch("app.routers.ws.radio_manager") as mock_ws_rm,
            patch("app.routers.health.radio_manager") as mock_health_rm,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.health.os.path.getsize", return_value=0),
        ):
            mock_ws_rm.is_connected = False
            mock_ws_rm.connection_info = None
            mock_health_rm.is_connected = False
            mock_health_rm.connection_info = None
            mock_health_rm.is_setup_in_progress = False
            mock_health_rm.is_setup_complete = False
            mock_health_rm.connection_desired = True
            mock_health_rm.is_reconnecting = False
            mock_health_rm.device_info_loaded = False
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
            mock_settings.database_path = "/tmp/test.db"
            mock_settings.disable_bots = False

            from app.main import app

            client = TestClient(app)

            with client.websocket_connect("/api/ws") as ws:
                data = ws.receive_json()

                assert data["type"] == "health"
                health = data["data"]
                assert health["radio_connected"] is False
                assert health["connection_info"] is None
                assert health["status"] == "degraded"

    def test_ping_returns_pong(self):
        """Sending 'ping' text receives a JSON pong response."""
        with (
            patch("app.routers.ws.radio_manager") as mock_ws_rm,
            patch("app.routers.health.radio_manager") as mock_health_rm,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.health.os.path.getsize", return_value=0),
        ):
            mock_ws_rm.is_connected = True
            mock_ws_rm.connection_info = "TCP: 192.168.1.1:4000"
            mock_health_rm.is_connected = True
            mock_health_rm.connection_info = "TCP: 192.168.1.1:4000"
            mock_health_rm.is_setup_in_progress = False
            mock_health_rm.is_setup_complete = True
            mock_health_rm.connection_desired = True
            mock_health_rm.is_reconnecting = False
            mock_health_rm.device_info_loaded = False
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
            mock_settings.database_path = "/tmp/test.db"
            mock_settings.disable_bots = False

            from app.main import app

            client = TestClient(app)

            with client.websocket_connect("/api/ws") as ws:
                # Consume the initial health message
                ws.receive_json()

                # Send ping and verify pong
                ws.send_text("ping")
                pong = ws.receive_json()

                assert pong == {"type": "pong"}

    def test_non_ping_message_does_not_produce_response(self):
        """Messages other than 'ping' are silently ignored (no response sent)."""
        with (
            patch("app.routers.ws.radio_manager") as mock_ws_rm,
            patch("app.routers.health.radio_manager") as mock_health_rm,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.health.os.path.getsize", return_value=0),
        ):
            mock_ws_rm.is_connected = True
            mock_ws_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_connected = True
            mock_health_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_setup_in_progress = False
            mock_health_rm.is_setup_complete = True
            mock_health_rm.connection_desired = True
            mock_health_rm.is_reconnecting = False
            mock_health_rm.device_info_loaded = False
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
            mock_settings.database_path = "/tmp/test.db"
            mock_settings.disable_bots = False

            from app.main import app

            client = TestClient(app)

            with client.websocket_connect("/api/ws") as ws:
                ws.receive_json()  # consume health

                # Send a non-ping message, then a ping to verify the connection
                # is still alive and only the ping produces a response
                ws.send_text("hello")
                ws.send_text("ping")
                pong = ws.receive_json()
                assert pong == {"type": "pong"}

    def test_disconnect_removes_client_from_manager(self):
        """Closing the WebSocket removes the connection from ws_manager."""
        with (
            patch("app.routers.ws.radio_manager") as mock_ws_rm,
            patch("app.routers.health.radio_manager") as mock_health_rm,
            patch("app.routers.health.RawPacketRepository") as mock_repo,
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.health.os.path.getsize", return_value=0),
        ):
            mock_ws_rm.is_connected = True
            mock_ws_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_connected = True
            mock_health_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_health_rm.is_setup_in_progress = False
            mock_health_rm.is_setup_complete = True
            mock_health_rm.connection_desired = True
            mock_health_rm.is_reconnecting = False
            mock_health_rm.device_info_loaded = False
            mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
            mock_settings.database_path = "/tmp/test.db"
            mock_settings.disable_bots = False

            from app.main import app

            client = TestClient(app)

            with client.websocket_connect("/api/ws") as ws:
                ws.receive_json()  # consume health
                assert len(ws_manager.active_connections) == 1

            # After context manager exits, the WebSocket is closed
            assert len(ws_manager.active_connections) == 0
