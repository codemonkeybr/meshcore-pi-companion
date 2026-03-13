"""Tests for RadioManager multi-transport connect dispatch, serial device
testing, and post-connect setup ordering.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRadioManagerConnect:
    """Test that connect() dispatches to the correct transport."""

    @pytest.mark.asyncio
    async def test_connect_serial_explicit_port(self):
        """Serial connect with explicit port sets connection_info."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_settings.connection_type = "serial"
            mock_settings.serial_port = "/dev/ttyUSB0"
            mock_settings.serial_baudrate = 115200
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_serial.assert_awaited_once_with(
                port="/dev/ttyUSB0",
                baudrate=115200,
                auto_reconnect=True,
                max_reconnect_attempts=10,
            )
            assert rm.connection_info == "Serial: /dev/ttyUSB0"
            assert rm.backend is not None
            assert rm.backend._mc is mock_mc

    @pytest.mark.asyncio
    async def test_connect_serial_autodetect(self):
        """Serial connect without port auto-detects."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.MeshCore") as mock_meshcore,
            patch("app.radio.find_radio_port", new_callable=AsyncMock) as mock_find,
        ):
            mock_settings.connection_type = "serial"
            mock_settings.serial_port = ""
            mock_settings.serial_baudrate = 115200
            mock_find.return_value = "/dev/ttyACM0"
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_find.assert_awaited_once_with(115200)
            assert rm.connection_info == "Serial: /dev/ttyACM0"

    @pytest.mark.asyncio
    async def test_connect_serial_autodetect_fails(self):
        """Serial auto-detect returns without raising when no radio found."""
        from app.radio import RadioManager

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.find_radio_port", new_callable=AsyncMock) as mock_find,
        ):
            mock_settings.connection_type = "serial"
            mock_settings.serial_port = ""
            mock_settings.serial_baudrate = 115200
            mock_find.return_value = None

            rm = RadioManager()
            await rm.connect()
            assert rm.backend is None
            assert not rm.is_connected
            mock_find.assert_awaited_once_with(115200)

    @pytest.mark.asyncio
    async def test_connect_tcp(self):
        """TCP connect sets connection_info with host:port."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_settings.connection_type = "tcp"
            mock_settings.tcp_host = "192.168.1.100"
            mock_settings.tcp_port = 4000
            mock_meshcore.create_tcp = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_tcp.assert_awaited_once_with(
                host="192.168.1.100",
                port=4000,
                auto_reconnect=True,
                max_reconnect_attempts=10,
            )
            assert rm.connection_info == "TCP: 192.168.1.100:4000"
            assert rm.backend is not None
            assert rm.backend._mc is mock_mc

    @pytest.mark.asyncio
    async def test_connect_ble(self):
        """BLE connect sets connection_info with address."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_settings.connection_type = "ble"
            mock_settings.ble_address = "AA:BB:CC:DD:EE:FF"
            mock_settings.ble_pin = "123456"
            mock_meshcore.create_ble = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_ble.assert_awaited_once_with(
                address="AA:BB:CC:DD:EE:FF",
                pin="123456",
                auto_reconnect=True,
                max_reconnect_attempts=15,
            )
            assert rm.connection_info == "BLE: AA:BB:CC:DD:EE:FF"
            assert rm.backend is not None
            assert rm.backend._mc is mock_mc

    @pytest.mark.asyncio
    async def test_connect_disconnects_existing_first(self):
        """Calling connect() when already connected disconnects first."""
        from app.radio import RadioManager

        old_mc = MagicMock()
        old_mc.disconnect = AsyncMock()
        new_mc = MagicMock()
        new_mc.is_connected = True

        with (
            patch("app.radio.settings") as mock_settings,
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_settings.connection_type = "tcp"
            mock_settings.tcp_host = "10.0.0.1"
            mock_settings.tcp_port = 4000
            mock_meshcore.create_tcp = AsyncMock(return_value=new_mc)

            rm = RadioManager()
            rm._backend = old_mc

            await rm.connect()

            old_mc.disconnect.assert_awaited_once()
            assert rm.backend is not None
            assert rm.backend._mc is new_mc


class TestConnectionMonitor:
    """Tests for the background connection monitor loop."""

    @pytest.mark.asyncio
    async def test_monitor_does_not_mark_connected_when_setup_fails(self):
        """A reconnect with failing post-connect setup should not broadcast healthy status."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyUSB0"
        rm._last_connected = True
        rm._backend = MagicMock()
        rm._backend.is_connected = False

        reconnect_calls = 0

        async def _reconnect(*args, **kwargs):
            nonlocal reconnect_calls
            reconnect_calls += 1
            if reconnect_calls == 1:
                rm._backend = MagicMock()
                rm._backend.is_connected = True
                return True
            return False

        sleep_calls = 0

        async def _sleep(_seconds: float):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 3:
                raise asyncio.CancelledError()

        rm.reconnect = AsyncMock(side_effect=_reconnect)
        rm.post_connect_setup = AsyncMock(side_effect=RuntimeError("setup failed"))

        with (
            patch("app.radio.asyncio.sleep", side_effect=_sleep),
            patch("app.websocket.broadcast_health") as mock_broadcast_health,
        ):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        # Should report connection lost, but not report healthy until setup succeeds.
        mock_broadcast_health.assert_any_call(False, "Serial: /dev/ttyUSB0")
        healthy_calls = [c for c in mock_broadcast_health.call_args_list if c.args[0] is True]
        assert healthy_calls == []
        assert rm._last_connected is False

    @pytest.mark.asyncio
    async def test_monitor_retries_setup_when_connected_but_incomplete(self):
        """Monitor retries setup when transport is connected but setup previously failed."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "TCP: test:4000"

        # Simulate: transport connected, _last_connected=True (set by _connect_*),
        # but setup failed so _setup_complete=False.
        mock_mc = MagicMock()
        mock_mc.is_connected = True
        rm._backend = mock_mc
        rm._last_connected = True
        rm._setup_complete = False

        setup_calls = 0

        async def _mock_setup():
            nonlocal setup_calls
            setup_calls += 1
            if setup_calls == 1:
                raise RuntimeError("setup failed")
            # Second call succeeds
            rm._setup_complete = True

        rm.post_connect_setup = AsyncMock(side_effect=_mock_setup)

        sleep_count = 0

        async def _sleep(_seconds: float):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 4:
                raise asyncio.CancelledError()

        with (
            patch("app.radio.asyncio.sleep", side_effect=_sleep),
            patch("app.websocket.broadcast_health") as mock_broadcast,
        ):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        # Setup should have been retried and eventually succeeded
        assert setup_calls >= 2
        # Should broadcast healthy after setup succeeds
        mock_broadcast.assert_any_call(True, "TCP: test:4000")
        assert rm._setup_complete is True


class TestReconnectLock:
    """Tests for reconnect() lock serialization — no duplicate reconnections."""

    @pytest.mark.asyncio
    async def test_concurrent_reconnects_only_connect_once(self):
        """Two concurrent reconnect() calls should only call connect() once."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._backend = None

        connect_count = 0

        async def mock_connect():
            nonlocal connect_count
            connect_count += 1
            # Simulate connect taking some time
            await asyncio.sleep(0.05)
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._backend = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            result_a, result_b = await asyncio.gather(
                rm.reconnect(broadcast_on_success=False),
                rm.reconnect(broadcast_on_success=False),
            )

        # First caller does the real connect, second sees is_connected=True
        assert connect_count == 1
        assert result_a is True
        assert result_b is True

    @pytest.mark.asyncio
    async def test_second_reconnect_skips_when_first_succeeds(self):
        """Second caller returns True without connecting when first already succeeded."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._backend = None

        call_order: list[str] = []

        async def mock_connect():
            call_order.append("connect")
            await asyncio.sleep(0.05)
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._backend = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            await asyncio.gather(
                rm.reconnect(broadcast_on_success=False),
                rm.reconnect(broadcast_on_success=False),
            )

        # connect should appear exactly once
        assert call_order == ["connect"]

    @pytest.mark.asyncio
    async def test_reconnect_retries_after_first_failure(self):
        """If first reconnect fails, a subsequent call should attempt connect again."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._backend = None

        attempt = 0

        async def mock_connect():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                # First attempt fails
                return
            # Second attempt succeeds
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._backend = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            result1 = await rm.reconnect(broadcast_on_success=False)
            assert result1 is False
            assert attempt == 1

            result2 = await rm.reconnect(broadcast_on_success=False)
            assert result2 is True
            assert attempt == 2


class TestSerialDeviceProbe:
    """Tests for test_serial_device() — verifies cleanup on all exit paths."""

    @pytest.mark.asyncio
    async def test_success_returns_true_and_disconnects(self):
        """Successful probe returns True and always disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = {"name": "MyNode"}
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is True
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_connected_returns_false_and_disconnects(self):
        """Device that connects but reports is_connected=False still disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = False
        mock_mc.self_info = None
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_self_info_returns_false_and_disconnects(self):
        """Connected but no self_info returns False; still disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = None
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_false_no_disconnect_needed(self):
        """asyncio.TimeoutError before create_serial completes — mc is None, no disconnect."""
        from app.radio import test_serial_device

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(side_effect=asyncio.TimeoutError)

            result = await test_serial_device("/dev/ttyUSB0", 115200, timeout=0.1)

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false_and_disconnects(self):
        """If create_serial succeeds but subsequent code raises, disconnect still runs."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        # Accessing is_connected raises (simulates corrupted state)
        type(mock_mc).is_connected = property(lambda self: (_ for _ in ()).throw(OSError("oops")))
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_exception_is_swallowed(self):
        """If disconnect() itself raises, the exception does not propagate."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = {"name": "MyNode"}
        mock_mc.disconnect = AsyncMock(side_effect=OSError("port closed"))

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        # Should still return True despite disconnect failure
        assert result is True
        mock_mc.disconnect.assert_awaited_once()


class TestPostConnectSetupOrdering:
    """Tests for post_connect_setup() — verifies drain-before-auto-fetch ordering."""

    @pytest.mark.asyncio
    async def test_drain_runs_before_auto_fetch(self):
        """drain_pending_messages must be called BEFORE start_auto_message_fetching."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()
        mock_mc.query_path_hash_mode = AsyncMock(return_value=(0, False))
        rm._backend = mock_mc

        call_order = []

        async def mock_drain(mc):
            call_order.append("drain")
            return 0

        async def mock_start_auto():
            call_order.append("auto_fetch")

        mock_mc.start_auto_message_fetching = AsyncMock(side_effect=mock_start_auto)

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch(
                "app.radio_sync.drain_pending_messages",
                new_callable=AsyncMock,
                side_effect=mock_drain,
            ),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        assert call_order == ["drain", "auto_fetch"], (
            f"Expected drain before auto_fetch, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_setup_sets_and_clears_in_progress_flag(self):
        """is_setup_in_progress is True during setup and False after."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()
        mock_mc.query_path_hash_mode = AsyncMock(return_value=(0, False))
        rm._backend = mock_mc

        observed_during = None

        async def mock_drain(mc):
            nonlocal observed_during
            observed_during = rm.is_setup_in_progress
            return 0

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch(
                "app.radio_sync.drain_pending_messages",
                new_callable=AsyncMock,
                side_effect=mock_drain,
            ),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        assert observed_during is True
        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_setup_clears_in_progress_flag_on_failure(self):
        """is_setup_in_progress is cleared even if setup raises."""
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        rm._backend = mock_mc

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch(
                "app.radio_sync.sync_radio_time",
                new_callable=AsyncMock,
                side_effect=RuntimeError("clock failed"),
            ),
        ):
            with pytest.raises(RuntimeError, match="clock failed"):
                await rm.post_connect_setup()

        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_setup_noop_when_no_meshcore(self):
        """post_connect_setup does nothing when meshcore is None."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._backend = None

        # Should not raise or call any functions
        await rm.post_connect_setup()
        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_flood_scope_applied_during_setup(self):
        """Non-empty flood_scope from settings is applied during post_connect_setup."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()
        mock_mc.query_path_hash_mode = AsyncMock(return_value=(0, False))
        rm._backend = mock_mc

        mock_settings = AppSettings(flood_scope="#TestRegion")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_plain_flood_scope_is_normalized_during_setup(self):
        """Legacy/plain stored flood_scope is normalized before applying to radio."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()
        mock_mc.query_path_hash_mode = AsyncMock(return_value=(0, False))
        rm._backend = mock_mc

        mock_settings = AppSettings(flood_scope="TestRegion")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_flood_scope_empty_resets_during_setup(self):
        """Empty flood_scope calls set_flood_scope("") during post_connect_setup."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()
        mock_mc.query_path_hash_mode = AsyncMock(return_value=(0, False))
        rm._backend = mock_mc

        mock_settings = AppSettings(flood_scope="")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.set_flood_scope.assert_awaited_once_with("")
