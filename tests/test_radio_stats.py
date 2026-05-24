import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import radio_stats


def _make_event(event_type, payload=None):
    return SimpleNamespace(type=event_type, payload=payload or {})


class TestRadioStatsSamplingLoop:
    @pytest.mark.asyncio
    async def test_logs_and_continues_after_unexpected_sample_exception(self):
        sample_calls = 0
        sleep_calls = 0

        async def fake_sample():
            nonlocal sample_calls
            sample_calls += 1
            if sample_calls == 1:
                raise RuntimeError("boom")
            return {}

        async def fake_sleep(_seconds: int) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 2:
                raise asyncio.CancelledError()

        mock_fanout = MagicMock()
        mock_fanout.broadcast_health_fanout = AsyncMock()

        with (
            patch.object(radio_stats, "_sample_all_stats", side_effect=fake_sample),
            patch.object(radio_stats.asyncio, "sleep", side_effect=fake_sleep),
            patch.object(radio_stats.logger, "exception") as mock_exception,
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            with pytest.raises(asyncio.CancelledError):
                await radio_stats._stats_sampling_loop()

        assert sample_calls == 2
        assert sleep_calls == 2
        mock_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcasts_health_every_cycle(self):
        """The loop should push a WS health broadcast and fanout after every iteration."""
        sleep_calls = 0

        async def fake_sample():
            return {}

        async def fake_sleep(_seconds: int) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 2:
                raise asyncio.CancelledError()

        mock_fanout = MagicMock()
        mock_fanout.broadcast_health_fanout = AsyncMock()

        with (
            patch.object(radio_stats, "_sample_all_stats", side_effect=fake_sample),
            patch.object(radio_stats.asyncio, "sleep", side_effect=fake_sleep),
            patch("app.websocket.broadcast_health") as mock_broadcast,
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            with pytest.raises(asyncio.CancelledError):
                await radio_stats._stats_sampling_loop()

        assert mock_broadcast.call_count == 2
        assert mock_fanout.broadcast_health_fanout.call_count == 2

    @pytest.mark.asyncio
    async def test_fanout_receives_enriched_payload(self):
        """The health fanout payload should include radio identity + stats."""
        sleep_calls = 0
        fake_snapshot = {
            "timestamp": 1700000000,
            "battery_mv": 4100,
            "uptime_secs": 3600,
            "noise_floor": -118,
            "last_rssi": -85,
            "last_snr": 9.5,
            "tx_air_secs": 100,
            "rx_air_secs": 200,
            "packets": {"recv": 500, "sent": 250},
        }

        async def fake_sample():
            return dict(fake_snapshot)

        async def fake_sleep(_seconds: int) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            raise asyncio.CancelledError()

        mock_fanout = MagicMock()
        mock_fanout.broadcast_health_fanout = AsyncMock()

        with (
            patch.object(radio_stats, "_sample_all_stats", side_effect=fake_sample),
            patch.object(radio_stats.asyncio, "sleep", side_effect=fake_sleep),
            patch("app.websocket.broadcast_health"),
            patch("app.fanout.manager.fanout_manager", mock_fanout),
            patch.object(radio_stats, "radio_manager") as mock_rm,
        ):
            mock_rm.is_connected = True
            mock_rm.connection_info = "Serial: /dev/ttyUSB0"
            mock_rm.meshcore = MagicMock()
            mock_rm.meshcore.self_info = {"public_key": "aabbccddeeff", "name": "MyRadio"}

            with pytest.raises(asyncio.CancelledError):
                await radio_stats._stats_sampling_loop()

        payload = mock_fanout.broadcast_health_fanout.call_args[0][0]
        assert payload["connected"] is True
        assert payload["public_key"] == "aabbccddeeff"
        assert payload["name"] == "MyRadio"
        assert payload["battery_mv"] == 4100
        assert payload["noise_floor_dbm"] == -118
        assert payload["packets_recv"] == 500


class TestSampleAllStats:
    @pytest.mark.asyncio
    async def test_returns_empty_when_disconnected(self):
        """Should return empty dict when radio is disconnected."""
        with patch.object(radio_stats, "radio_manager") as mock_rm:
            mock_rm.is_connected = False
            result = await radio_stats._sample_all_stats()

        assert result == {}

    @pytest.mark.asyncio
    async def test_partial_stats_still_records_available_data(self):
        """If core stats return ERROR but radio/packet stats succeed, noise floor
        is still sampled and available fields are returned."""
        from meshcore import EventType

        radio_stats._noise_floor_samples.clear()

        core_event = _make_event(EventType.ERROR, {"reason": "unsupported"})
        radio_event = _make_event(
            EventType.STATS_RADIO,
            {
                "noise_floor": -118,
                "last_rssi": -90,
                "last_snr": 8.0,
                "tx_air_secs": 10,
                "rx_air_secs": 20,
            },
        )
        packet_event = _make_event(
            EventType.STATS_PACKETS,
            {
                "recv": 100,
                "sent": 50,
                "flood_tx": 20,
                "direct_tx": 30,
                "flood_rx": 60,
                "direct_rx": 40,
            },
        )

        mock_mc = AsyncMock()
        mock_mc.commands.get_stats_core = AsyncMock(return_value=core_event)
        mock_mc.commands.get_stats_radio = AsyncMock(return_value=radio_event)
        mock_mc.commands.get_stats_packets = AsyncMock(return_value=packet_event)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mc)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(radio_stats, "radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.radio_operation = MagicMock(return_value=mock_ctx)
            snapshot = await radio_stats._sample_all_stats()

        # Core fields missing (ERROR), but radio + packet fields present
        assert "battery_mv" not in snapshot
        assert snapshot["noise_floor"] == -118
        assert snapshot["packets"]["recv"] == 100
        # Noise floor history was still appended
        assert len(radio_stats._noise_floor_samples) == 1

    @pytest.mark.asyncio
    async def test_all_stats_succeed(self):
        """All three stats commands succeed — full snapshot returned."""
        from meshcore import EventType

        radio_stats._noise_floor_samples.clear()

        core_event = _make_event(
            EventType.STATS_CORE,
            {"battery_mv": 4100, "uptime_secs": 7200, "errors": 0, "queue_len": 2},
        )
        radio_event = _make_event(
            EventType.STATS_RADIO,
            {
                "noise_floor": -120,
                "last_rssi": -85,
                "last_snr": 9.5,
                "tx_air_secs": 100,
                "rx_air_secs": 200,
            },
        )
        packet_event = _make_event(
            EventType.STATS_PACKETS,
            {
                "recv": 500,
                "sent": 250,
                "flood_tx": 100,
                "direct_tx": 150,
                "flood_rx": 300,
                "direct_rx": 200,
            },
        )

        mock_mc = AsyncMock()
        mock_mc.commands.get_stats_core = AsyncMock(return_value=core_event)
        mock_mc.commands.get_stats_radio = AsyncMock(return_value=radio_event)
        mock_mc.commands.get_stats_packets = AsyncMock(return_value=packet_event)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mc)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(radio_stats, "radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.radio_operation = MagicMock(return_value=mock_ctx)
            snapshot = await radio_stats._sample_all_stats()

        assert snapshot["battery_mv"] == 4100
        assert snapshot["noise_floor"] == -120
        assert snapshot["packets"]["sent"] == 250
        assert len(radio_stats._noise_floor_samples) == 1

    @pytest.mark.asyncio
    async def test_all_errors_returns_empty(self):
        """If every stats command returns ERROR, result is empty."""
        from meshcore import EventType

        error = _make_event(EventType.ERROR, {"reason": "unsupported"})

        mock_mc = AsyncMock()
        mock_mc.commands.get_stats_core = AsyncMock(return_value=error)
        mock_mc.commands.get_stats_radio = AsyncMock(return_value=error)
        mock_mc.commands.get_stats_packets = AsyncMock(return_value=error)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_mc)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(radio_stats, "radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.radio_operation = MagicMock(return_value=mock_ctx)
            snapshot = await radio_stats._sample_all_stats()

        assert snapshot == {}
