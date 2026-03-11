"""Tests for health endpoint fanout status fields.

Verifies that build_health_data correctly reports fanout module statuses
via the fanout_manager.
"""

from unittest.mock import patch

import pytest

from app.routers.health import build_health_data


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
            "uuid-1": {"name": "Private MQTT", "type": "mqtt_private", "status": "connected"},
            "uuid-2": {
                "name": "Community MQTT",
                "type": "mqtt_community",
                "status": "disconnected",
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
        ):
            mock_rm.is_setup_in_progress = False
            mock_rm.is_setup_complete = True
            data = await build_health_data(True, "Serial: /dev/ttyUSB0")

        assert data["status"] == "ok"
        assert data["radio_connected"] is True
        assert data["radio_initializing"] is False
        assert data["connection_info"] == "Serial: /dev/ttyUSB0"

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

    @pytest.mark.asyncio
    async def test_health_setup_required_false_when_not_spi(self, test_db):
        """setup_required is False when not in SPI mode."""
        with patch("app.routers.health.settings") as mock_settings:
            mock_settings.connection_type = "serial"
            mock_settings.database_path = "data/meshcore.db"
            mock_settings.disable_bots = False
            with patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted",
                return_value=None,
            ):
                data = await build_health_data(True, "Serial: /dev/ttyUSB0")
        assert data["setup_required"] is False

    @pytest.mark.asyncio
    async def test_health_setup_required_true_when_spi_config_invalid(self, test_db):
        """setup_required is True when SPI mode is selected but config is invalid."""
        with (
            patch("app.routers.health.settings") as mock_settings,
            patch("app.routers.setup.get_setup_required", return_value=True),
        ):
            mock_settings.connection_type = "spi"
            mock_settings.database_path = "data/meshcore.db"
            mock_settings.disable_bots = False
            with patch(
                "app.routers.health.RawPacketRepository.get_oldest_undecrypted",
                return_value=None,
            ):
                data = await build_health_data(True, "SPI")
        assert data["setup_required"] is True
