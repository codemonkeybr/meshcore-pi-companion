"""Tests for health endpoint fanout status fields.

Verifies that build_health_data correctly reports fanout module statuses
via the fanout_manager.
"""

from unittest.mock import patch

import pytest

from app.routers.health import build_health_data
from app.version_info import AppBuildInfo


class TestHealthFanoutStatus:
    """Test fanout_statuses in build_health_data."""

    @pytest.mark.asyncio
    async def test_no_fanout_modules_returns_empty(self, test_db):
        """fanout_statuses should be empty dict when no modules are running."""
        with patch("app.fanout.manager.fanout_manager") as mock_fm:
            mock_fm.get_statuses.return_value = {}
            data = await build_health_data(True, "TCP: 1.2.3.4:4000")

        assert data["fanout_statuses"] == {}

    @pytest.mark.asyncio
    async def test_fanout_statuses_reflect_manager(self, test_db):
        """fanout_statuses should return whatever the manager reports."""
        mock_statuses = {
            "uuid-1": {
                "name": "Private MQTT",
                "type": "mqtt_private",
                "status": "connected",
                "last_error": None,
            },
            "uuid-2": {
                "name": "Community MQTT",
                "type": "mqtt_community",
                "status": "error",
                "last_error": "auth failed",
            },
        }
        with patch("app.fanout.manager.fanout_manager") as mock_fm:
            mock_fm.get_statuses.return_value = mock_statuses
            data = await build_health_data(True, "Serial: /dev/ttyUSB0")

        assert data["fanout_statuses"] == mock_statuses

    @pytest.mark.asyncio
    async def test_health_status_ok_when_connected(self, test_db):
        """Health status is 'ok' when radio is connected."""
        with (
            patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
            ),
            patch("app.routers.health.radio_manager") as mock_rm,
            patch(
                "app.routers.health.get_app_build_info",
                return_value=AppBuildInfo(
                    version="3.4.1",
                    version_source="pyproject",
                    commit_hash="abcdef12",
                    commit_source="git",
                ),
            ),
        ):
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            data = await build_health_data(True, "Serial: /dev/ttyUSB0")

        assert data["status"] == "ok"
        assert data["radio_connected"] is True
        assert data["radio_initializing"] is False
        assert data["radio_state"] == "connected"
        assert data["connection_info"] == "Serial: /dev/ttyUSB0"
        assert data["app_info"] == {
            "version": "3.4.1",
            "commit_hash": "abcdef12",
        }

    @pytest.mark.asyncio
    async def test_health_includes_cached_radio_device_info(self, test_db):
        """Health includes device metadata captured during post-connect setup."""
        with (
            patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
            ),
            patch("app.routers.health.radio_manager") as mock_rm,
        ):
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = False
            mock_rm.device_info_loaded = True
            mock_rm.device_model = "T-Echo"
            mock_rm.firmware_build = "2025-02-01"
            mock_rm.firmware_version = "1.2.3"
            mock_rm.max_contacts = 350
            mock_rm.max_channels = 64
            data = await build_health_data(True, "Serial: /dev/ttyUSB0")

        assert data["radio_device_info"] == {
            "model": "T-Echo",
            "firmware_build": "2025-02-01",
            "firmware_version": "1.2.3",
            "max_contacts": 350,
            "max_channels": 64,
        }

    @pytest.mark.asyncio
    async def test_health_status_degraded_when_disconnected(self, test_db):
        """Health status is 'degraded' when radio is disconnected."""
        with patch(
            "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
        ):
            data = await build_health_data(False, None)

        assert data["status"] == "degraded"
        assert data["radio_connected"] is False
        assert data["radio_initializing"] is False
        assert data["radio_state"] == "disconnected"
        assert data["connection_info"] is None

    @pytest.mark.asyncio
    async def test_health_status_degraded_while_radio_initializing(self, test_db):
        """Health stays degraded while transport is up but post-connect setup is incomplete."""
        with (
            patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
            ),
            patch("app.routers.health.radio_manager") as mock_rm,
        ):
            mock_rm.is_setup_in_progress = True
            mock_rm.is_setup_complete = False
            data = await build_health_data(True, "Serial: /dev/ttyUSB0")

        assert data["status"] == "degraded"
        assert data["radio_connected"] is True
        assert data["radio_initializing"] is True
        assert data["radio_state"] == "initializing"

    @pytest.mark.asyncio
    async def test_health_state_paused_when_operator_disabled_connection(self, test_db):
        """Health reports paused when the operator has disabled reconnect attempts."""
        with (
            patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
            ),
            patch("app.routers.health.radio_manager") as mock_rm,
        ):
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = False
            mock_rm.connection_desired = False
            mock_rm.is_reconnecting = False
            data = await build_health_data(False, "BLE: AA:BB:CC:DD:EE:FF")

        assert data["radio_state"] == "paused"
        assert data["radio_connected"] is False

    @pytest.mark.asyncio
    async def test_health_state_connecting_while_reconnect_in_progress(self, test_db):
        """Health reports connecting while retries are active but transport is not up yet."""
        with (
            patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted", return_value=None
            ),
            patch("app.routers.health.radio_manager") as mock_rm,
        ):
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = False
            mock_rm.connection_desired = True
            mock_rm.is_reconnecting = True
            data = await build_health_data(False, None)

        assert data["radio_state"] == "connecting"
        assert data["radio_connected"] is False
