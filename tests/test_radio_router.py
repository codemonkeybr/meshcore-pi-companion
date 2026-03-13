"""Tests for radio router endpoint logic."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType
from pydantic import ValidationError

from app.radio import RadioManager, radio_manager
from app.routers.radio import (
    PrivateKeyUpdate,
    RadioConfigResponse,
    RadioConfigUpdate,
    RadioSettings,
    disconnect_radio,
    get_radio_config,
    reboot_radio,
    reconnect_radio,
    send_advertisement,
    set_private_key,
    update_radio_config,
)
from app.services.radio_runtime import RadioRuntime


def _radio_result(event_type=EventType.OK, payload=None):
    result = MagicMock()
    result.type = event_type
    result.payload = payload or {}
    return result


def _noop_radio_operation(mc=None):
    """Factory for a no-op radio_operation context manager that yields mc."""

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield mc

    return _ctx


def _runtime(manager):
    return RadioRuntime(lambda: manager)


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


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
        "adv_loc_policy": 2,
    }
    mc.commands = MagicMock()
    mc.commands.set_name = AsyncMock()
    mc.commands.set_coords = AsyncMock()
    mc.commands.set_tx_power = AsyncMock()
    mc.commands.set_radio = AsyncMock()
    mc.commands.set_advert_loc_policy = AsyncMock(return_value=_radio_result())
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
        assert response.advert_location_source == "current"

    @pytest.mark.asyncio
    async def test_maps_any_nonzero_advert_location_policy_to_current(self):
        mc = _mock_meshcore_with_info()
        mc.self_info["adv_loc_policy"] = 1

        with patch("app.routers.radio.require_connected", return_value=mc):
            response = await get_radio_config()

        assert response.advert_location_source == "current"

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
            patch.object(radio_manager, "_meshcore", mc),
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

    @pytest.mark.asyncio
    async def test_updates_advert_location_source(self):
        mc = _mock_meshcore_with_info()
        expected = RadioConfigResponse(
            public_key="aa" * 32,
            name="NodeA",
            lat=10.0,
            lon=20.0,
            tx_power=17,
            max_tx_power=22,
            radio=RadioSettings(freq=910.525, bw=62.5, sf=7, cr=5),
            path_hash_mode=0,
            path_hash_mode_supported=False,
            advert_location_source="current",
        )

        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch("app.routers.radio.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.routers.radio.get_radio_config", new_callable=AsyncMock, return_value=expected
            ),
        ):
            result = await update_radio_config(RadioConfigUpdate(advert_location_source="current"))

        mc.commands.set_advert_loc_policy.assert_awaited_once_with(1)
        assert result == expected

    def test_model_rejects_negative_path_hash_mode(self):
        with pytest.raises(ValidationError):
            RadioConfigUpdate(path_hash_mode=-1)

    def test_model_rejects_too_large_path_hash_mode(self):
        with pytest.raises(ValidationError):
            RadioConfigUpdate(path_hash_mode=3)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path_hash_mode", [-1, 3, 999])
    async def test_endpoint_rejects_invalid_path_hash_mode(self, client, path_hash_mode):
        response = await client.patch("/api/radio/config", json={"path_hash_mode": path_hash_mode})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_path_hash_mode_when_firmware_does_not_support_it(self):
        mc = _mock_meshcore_with_info()

        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "path_hash_mode_supported", False),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_radio_config(RadioConfigUpdate(path_hash_mode=1))

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_propagates_radio_error_when_setting_path_hash_mode(self):
        mc = _mock_meshcore_with_info()
        mc.commands.set_path_hash_mode = AsyncMock(
            return_value=_radio_result(EventType.ERROR, {"error": "nope"})
        )

        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch.object(radio_manager, "path_hash_mode_supported", True),
            patch.object(radio_manager, "path_hash_mode", 0),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_radio_config(RadioConfigUpdate(path_hash_mode=1))

        assert exc.value.status_code == 500
        assert "Failed to set path hash mode" in str(exc.value.detail)
        assert radio_manager.path_hash_mode == 0
        mc.commands.send_appstart.assert_not_awaited()


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
        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_successful_import_refreshes_keystore(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_export,
        ):
            result = await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert result == {"status": "ok"}
        mock_export.assert_awaited_once_with(mc)

    @pytest.mark.asyncio
    async def test_import_ok_but_keystore_refresh_fails_returns_500(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_export,
        ):
            with pytest.raises(HTTPException) as exc:
                await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert exc.value.status_code == 500
        assert "keystore" in exc.value.detail.lower()
        # Called twice: initial attempt + one retry
        assert mock_export.await_count == 2

    @pytest.mark.asyncio
    async def test_keystore_refresh_succeeds_on_retry(self):
        mc = _mock_meshcore_with_info()
        mc.commands.import_private_key = AsyncMock(return_value=_radio_result())
        with (
            patch("app.routers.radio.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            patch(
                "app.keystore.export_and_store_private_key",
                new_callable=AsyncMock,
                side_effect=[False, True],
            ) as mock_export,
        ):
            result = await set_private_key(PrivateKeyUpdate(private_key="aa" * 64))

        assert result == {"status": "ok"}
        assert mock_export.await_count == 2


class TestAdvertise:
    @pytest.mark.asyncio
    async def test_raises_when_send_fails(self):
        radio_manager._meshcore = MagicMock()
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

    @pytest.mark.asyncio
    async def test_concurrent_advertise_calls_are_serialized(self):
        active = 0
        max_active = 0

        async def fake_send(mc, *, force: bool):
            nonlocal active, max_active
            assert force is True
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return True

        isolated_manager = RadioManager()
        isolated_manager._meshcore = MagicMock()
        with (
            patch("app.routers.radio.require_connected"),
            patch("app.routers.radio.radio_manager", _runtime(isolated_manager)),
            patch(
                "app.routers.radio.do_send_advertisement",
                new_callable=AsyncMock,
                side_effect=fake_send,
            ),
        ):
            await asyncio.gather(send_advertisement(), send_advertisement())

        assert max_active == 1


class TestRebootAndReconnect:
    @pytest.mark.asyncio
    async def test_reboot_connected_sends_reboot_command(self):
        mock_mc = MagicMock()
        mock_mc.commands.reboot = AsyncMock()

        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.meshcore = mock_mc
        mock_rm.radio_operation = _noop_radio_operation(mock_mc)

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reboot_radio()

        assert result["status"] == "ok"
        mock_mc.commands.reboot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reboot_returns_pending_when_reconnect_in_progress(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.meshcore = None
        mock_rm.is_reconnecting = True
        mock_rm.radio_operation = _noop_radio_operation()

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
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
        mock_rm.radio_operation = _noop_radio_operation()
        mock_rm.connection_info = "TCP: test:4000"

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reboot_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True
        mock_rm.reconnect.assert_awaited_once()
        mock_rm.post_connect_setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_returns_already_connected(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = True
        mock_rm.radio_operation = _noop_radio_operation()
        mock_rm.is_setup_complete = True

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            result = await reconnect_radio()

        assert result["status"] == "ok"
        assert result["connected"] is True

    @pytest.mark.asyncio
    async def test_reconnect_raises_503_on_failure(self):
        mock_rm = MagicMock()
        mock_rm.is_connected = False
        mock_rm.is_reconnecting = False
        mock_rm.reconnect = AsyncMock(return_value=False)
        mock_rm.radio_operation = _noop_radio_operation()

        with patch("app.routers.radio.radio_manager", _runtime(mock_rm)):
            with pytest.raises(HTTPException) as exc:
                await reconnect_radio()

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_disconnect_pauses_connection_attempts_and_broadcasts_health(self):
        mock_rm = MagicMock()
        mock_rm.pause_connection = AsyncMock()
        mock_rm.connection_info = "BLE: AA:BB:CC:DD:EE:FF"

        with (
            patch("app.routers.radio.radio_manager", _runtime(mock_rm)),
            patch("app.routers.radio.broadcast_health") as mock_broadcast,
        ):
            result = await disconnect_radio()

        assert result["status"] == "ok"
        assert result["connected"] is False
        assert result["paused"] is True
        mock_rm.pause_connection.assert_awaited_once()
        mock_broadcast.assert_called_once_with(False, "BLE: AA:BB:CC:DD:EE:FF")
