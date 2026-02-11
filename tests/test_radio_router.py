"""Tests for radio router endpoint logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.routers.radio import (
    PrivateKeyUpdate,
    RadioConfigResponse,
    RadioConfigUpdate,
    RadioSettings,
    get_radio_config,
    reboot_radio,
    reconnect_radio,
    send_advertisement,
    set_private_key,
    update_radio_config,
)


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


def _mock_meshcore_with_info():
    mc = MagicMock()
    mc.self_info = {
        "public_key": "aa" * 32,
        "name": "NodeA",
        "adv_lat": 10.0,
        "adv_lon": 20.0,
        "tx_power": 17,
        "max_tx_power": 22,
        "radio_freq": 910.525,
        "radio_bw": 62.5,
        "radio_sf": 7,
        "radio_cr": 5,
    }
    mc.commands = MagicMock()
    mc.commands.set_name = AsyncMock()
    mc.commands.set_coords = AsyncMock()
    mc.commands.set_tx_power = AsyncMock()
    mc.commands.set_radio = AsyncMock()
    mc.commands.send_appstart = AsyncMock()
    mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
    return mc


class TestGetRadioConfig:
    @pytest.mark.asyncio
    async def test_maps_self_info_to_response(self):
        mc = _mock_meshcore_with_info()
        with patch("app.routers.radio.require_connected", return_value=mc):
            response = await get_radio_config()

        assert response.public_key == "aa" * 32
        assert response.name == "NodeA"
        assert response.lat == 10.0
        assert response.lon == 20.0
        assert response.radio.freq == 910.525
        assert response.radio.cr == 5

    @pytest.mark.asyncio
    async def test_returns_503_when_self_info_missing(self):
        mc = MagicMock()
        mc.self_info = None
        with patch("app.routers.radio.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await get_radio_config()

        assert exc.value.status_code == 503


class TestUpdateRadioConfig:
    @pytest.mark.asyncio
    async def test_updates_only_requested_fields_and_refreshes_info(self):
        mc = _mock_meshcore_with_info()
        expected = RadioConfigResponse(
            public_key="aa" * 32,
            name="NodeUpdated",
            lat=1.23,
            lon=20.0,
            tx_power=17,
            max_tx_power=22,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
        )

        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch("app.routers.radio.sync_radio_time", new_callable=AsyncMock) as mock_sync_time,
            patch(
                "app.routers.radio.get_radio_config", new_callable=AsyncMock, return_value=expected
            ),
        ):
            result = await update_radio_config(RadioConfigUpdate(name="NodeUpdated", lat=1.23))

        mc.commands.set_name.assert_awaited_once_with("NodeUpdated")
        mc.commands.set_coords.assert_awaited_once_with(lat=1.23, lon=20.0)
        mc.commands.set_tx_power.assert_not_awaited()
        mc.commands.set_radio.assert_not_awaited()
        mc.commands.send_appstart.assert_awaited_once()
        mock_sync_time.assert_awaited_once()
        assert result == expected


class TestPrivateKeyImport:
    @pytest.mark.asyncio
    async def test_rejects_invalid_hex(self):
        mc = _mock_meshcore_with_info()
        with patch("app.routers.radio.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="not-hex"))

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_500_on_radio_error(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "failed"})
        )
        with patch("app.routers.radio.require_connected", return_value=mc):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert exc.value.status_code == 500


class TestAdvertise:
    @pytest.mark.asyncio
    async def test_raises_when_send_fails(self):
        with (
            patch("app.routers.radio.require_connected"),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_advertisement()

        assert exc.value.status_code == 500


class TestRebootAndReconnect:
    @pytest.mark.asyncio
    async def test_reboot_connected_sends_reboot_command(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.meshcore = MagicMock()
        mock_rm.meshcore.commands.reboot = AsyncMock()

        with patch("app.routers.radio.radio_manager", mock_rm):
            result = await reboot_radio()

        assert result["status"] == "ok"
        mock_rm.meshcore.commands.reboot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reboot_returns_pending_when_reconnect_in_progress(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.meshcore = None
        mock_rm.is_reconnecting = True

        with patch("app.routers.radio.radio_manager", mock_rm):
            result = await reboot_radio()

        assert result["status"] == "pending"
        assert result["connected"] is False

    @pytest.mark.asyncio
    async def test_reboot_attempts_reconnect_when_disconnected(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.meshcore = None
        mock_rm.is_reconnecting = False
        mock_rm.reconnect = AsyncMock(return_value=True)
        mock_rm.post_connect_setup = AsyncMock()

        with patch("app.routers.radio.radio_manager", mock_rm):
            result = await reboot_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True
        mock_rm.reconnect.assert_awaited_once()
        mock_rm.post_connect_setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_returns_already_connected(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = True

        with patch("app.routers.radio.radio_manager", mock_rm):
            result = await reconnect_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True

    @pytest.mark.asyncio
    async def test_reconnect_raises_503_on_failure(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.is_reconnecting = False
        mock_rm.reconnect = AsyncMock(return_value=False)

        with patch("app.routers.radio.radio_manager", mock_rm):
            with pytest.raises(HTTPException) as exc:
                await reconnect_radio()

        assert exc.value.status_code == 503
