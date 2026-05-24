"""Tests for on_rx_log_data event handler integration.

Verifies that the primary RF packet entry point correctly extracts hex payload,
SNR, and RSSI from MeshCore events and passes them to process_raw_packet.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType
from meshcore.packets import PacketType
from meshcore.reader import MessageReader


class TestOnRxLogData:
    """Test the on_rx_log_data event handler."""

    @pytest.mark.asyncio
    async def test_extracts_hex_and_calls_process_raw_packet(self):
        """Hex payload is converted to bytes and forwarded correctly."""
        from app.event_handlers import on_rx_log_data

        class MockEvent:
            payload = {
                "payload": "deadbeef01020304",
                "snr": 7.5,
                "rssi": -85,
            }

        with patch("app.event_handlers.process_raw_packet", new_callable=AsyncMock) as mock_process:
            await on_rx_log_data(MockEvent())

            mock_process.assert_called_once_with(
                raw_bytes=bytes.fromhex("deadbeef01020304"),
                snr=7.5,
                rssi=-85,
            )

    @pytest.mark.asyncio
    async def test_missing_payload_field_returns_early(self):
        """Event without 'payload' field is silently skipped."""
        from app.event_handlers import on_rx_log_data

        class MockEvent:
            payload = {"snr": 5.0, "rssi": -90}  # no 'payload' key

        with patch("app.event_handlers.process_raw_packet", new_callable=AsyncMock) as mock_process:
            await on_rx_log_data(MockEvent())

            mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_snr_rssi_passes_none(self):
        """Missing SNR and RSSI fields pass None to process_raw_packet."""
        from app.event_handlers import on_rx_log_data

        class MockEvent:
            payload = {"payload": "ff00"}

        with patch("app.event_handlers.process_raw_packet", new_callable=AsyncMock) as mock_process:
            await on_rx_log_data(MockEvent())

            mock_process.assert_called_once_with(
                raw_bytes=bytes.fromhex("ff00"),
                snr=None,
                rssi=None,
            )

    @pytest.mark.asyncio
    async def test_empty_hex_payload_produces_empty_bytes(self):
        """Empty hex string produces empty bytes (not an error)."""
        from app.event_handlers import on_rx_log_data

        class MockEvent:
            payload = {"payload": ""}

        with patch("app.event_handlers.process_raw_packet", new_callable=AsyncMock) as mock_process:
            await on_rx_log_data(MockEvent())

            mock_process.assert_called_once_with(
                raw_bytes=b"",
                snr=None,
                rssi=None,
            )

    @pytest.mark.asyncio
    async def test_invalid_hex_raises_valueerror(self):
        """Invalid hex payload raises ValueError (not silently swallowed)."""
        from app.event_handlers import on_rx_log_data

        class MockEvent:
            payload = {"payload": "not_valid_hex"}

        with pytest.raises(ValueError):
            await on_rx_log_data(MockEvent())

    @pytest.mark.asyncio
    async def test_real_meshcore_reader_forwards_3byte_log_data_to_handler(self):
        """The meshcore reader emits usable RX_LOG_DATA for 3-byte-hop packets."""
        from app.event_handlers import on_rx_log_data

        payload_hex = "15833fa002860ccae0eed9ca78b9ab0775d477c1f6490a398bf4edc75240"
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock()
        reader = MessageReader(dispatcher)

        frame = bytes(
            [
                PacketType.LOG_DATA.value,
                int(7.5 * 4),
                (-85) & 0xFF,
            ]
        ) + bytes.fromhex(payload_hex)

        await reader.handle_rx(bytearray(frame))

        dispatcher.dispatch.assert_awaited_once()
        event = dispatcher.dispatch.await_args.args[0]
        assert event.type == EventType.RX_LOG_DATA
        assert event.payload["payload"] == payload_hex.lower()
        assert event.payload["path_hash_size"] == 3
        assert event.payload["path_len"] == 3
        assert event.payload["path"] == "3fa002860ccae0eed9"
        assert event.payload["snr"] == 7.5
        assert event.payload["rssi"] == -85

        with patch("app.event_handlers.process_raw_packet", new_callable=AsyncMock) as mock_process:
            await on_rx_log_data(event)

            mock_process.assert_called_once_with(
                raw_bytes=bytes.fromhex(payload_hex),
                snr=7.5,
                rssi=-85,
            )
