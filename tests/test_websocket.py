"""Tests for WebSocket manager functionality."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.websocket import SEND_TIMEOUT_SECONDS, WebSocketManager


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocketManager for each test."""
    return WebSocketManager()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


class TestWebSocketBroadcast:
    """Tests for the broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_clients(self, ws_manager: WebSocketManager):
        """Broadcast should send message to all connected clients."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()

        await ws_manager.connect(ws1)
        await ws_manager.connect(ws2)

        await ws_manager.broadcast("test", {"key": "value"})

        # Both clients should receive the message
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

        # Verify the message format
        import json

        expected = json.dumps({"type": "test", "data": {"key": "value"}})
        ws1.send_text.assert_called_with(expected)
        ws2.send_text.assert_called_with(expected)

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_clients(self, ws_manager: WebSocketManager):
        """Clients that fail to receive should be removed."""
        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        good_ws.accept = AsyncMock()
        bad_ws.accept = AsyncMock()
        bad_ws.send_text.side_effect = Exception("Connection closed")

        await ws_manager.connect(good_ws)
        await ws_manager.connect(bad_ws)

        assert len(ws_manager.active_connections) == 2

        await ws_manager.broadcast("test", {})

        # Bad client should be removed
        assert len(ws_manager.active_connections) == 1
        assert good_ws in ws_manager.active_connections
        assert bad_ws not in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_handles_timeout(self, ws_manager: WebSocketManager):
        """Clients that timeout should be removed."""
        good_ws = AsyncMock()
        slow_ws = AsyncMock()
        good_ws.accept = AsyncMock()
        slow_ws.accept = AsyncMock()

        # Make slow_ws hang indefinitely
        async def slow_send(_):
            await asyncio.sleep(SEND_TIMEOUT_SECONDS + 1)

        slow_ws.send_text.side_effect = slow_send

        await ws_manager.connect(good_ws)
        await ws_manager.connect(slow_ws)

        assert len(ws_manager.active_connections) == 2

        # Broadcast should complete despite slow client (due to timeout)
        await ws_manager.broadcast("test", {})

        # Slow client should be removed due to timeout
        assert len(ws_manager.active_connections) == 1
        assert good_ws in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_concurrent_sends(self, ws_manager: WebSocketManager):
        """Verify that sends happen concurrently, not sequentially."""
        call_times = []

        async def record_send_time(ws_name):
            async def _send(_):
                call_times.append((ws_name, asyncio.get_event_loop().time()))
                await asyncio.sleep(0.1)  # Simulate some work

            return _send

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        ws3.accept = AsyncMock()

        ws1.send_text.side_effect = await record_send_time("ws1")
        ws2.send_text.side_effect = await record_send_time("ws2")
        ws3.send_text.side_effect = await record_send_time("ws3")

        await ws_manager.connect(ws1)
        await ws_manager.connect(ws2)
        await ws_manager.connect(ws3)

        start_time = asyncio.get_event_loop().time()
        await ws_manager.broadcast("test", {})
        elapsed = asyncio.get_event_loop().time() - start_time

        # If sequential: 3 * 0.1 = 0.3s
        # If concurrent: ~0.1s
        # Allow some margin for test overhead
        assert elapsed < 0.2, f"Sends should be concurrent, took {elapsed}s"

    @pytest.mark.asyncio
    async def test_broadcast_does_not_block_on_slow_client(self, ws_manager: WebSocketManager):
        """A slow client should not block messages to fast clients."""
        fast_ws = AsyncMock()
        slow_ws = AsyncMock()
        fast_ws.accept = AsyncMock()
        slow_ws.accept = AsyncMock()

        fast_received_at = None
        slow_received_at = None

        async def fast_send(_):
            nonlocal fast_received_at
            fast_received_at = asyncio.get_event_loop().time()

        async def slow_send(_):
            nonlocal slow_received_at
            await asyncio.sleep(0.2)  # Slow client
            slow_received_at = asyncio.get_event_loop().time()

        fast_ws.send_text.side_effect = fast_send
        slow_ws.send_text.side_effect = slow_send

        await ws_manager.connect(slow_ws)
        await ws_manager.connect(fast_ws)

        start_time = asyncio.get_event_loop().time()
        await ws_manager.broadcast("test", {})

        # Fast client should receive message quickly, not waiting for slow client
        assert fast_received_at is not None
        assert fast_received_at - start_time < 0.1, "Fast client was blocked by slow client"

    @pytest.mark.asyncio
    async def test_broadcast_empty_connections(self, ws_manager: WebSocketManager):
        """Broadcast should handle empty connection list gracefully."""
        # Should not raise
        await ws_manager.broadcast("test", {"data": "value"})


class TestWebSocketConnectionManagement:
    """Tests for connection/disconnection."""

    @pytest.mark.asyncio
    async def test_connect_adds_to_list(self, ws_manager: WebSocketManager, mock_websocket):
        """Connect should add websocket to active connections."""
        assert len(ws_manager.active_connections) == 0

        await ws_manager.connect(mock_websocket)

        assert len(ws_manager.active_connections) == 1
        assert mock_websocket in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_list(self, ws_manager: WebSocketManager, mock_websocket):
        """Disconnect should remove websocket from active connections."""
        await ws_manager.connect(mock_websocket)
        assert len(ws_manager.active_connections) == 1

        await ws_manager.disconnect(mock_websocket)

        assert len(ws_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_safe(
        self, ws_manager: WebSocketManager, mock_websocket
    ):
        """Disconnecting a non-connected websocket should not raise."""
        # Should not raise
        await ws_manager.disconnect(mock_websocket)
        assert len(ws_manager.active_connections) == 0


class TestBroadcastEventFanout:
    """Test that broadcast_event dispatches to WS and fanout manager."""

    @pytest.mark.asyncio
    async def test_broadcast_event_dispatches_to_ws_and_fanout(self):
        """broadcast_event creates a WS task and dispatches to fanout manager."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_fm.broadcast_message = AsyncMock()

            broadcast_event("message", {"id": 1, "text": "hello"})

            # Let the asyncio tasks run
            await asyncio.sleep(0)

            mock_ws.broadcast.assert_called_once_with("message", {"id": 1, "text": "hello"})
            mock_fm.broadcast_message.assert_called_once_with({"id": 1, "text": "hello"})

    @pytest.mark.asyncio
    async def test_broadcast_event_raw_packet_dispatches_to_fanout(self):
        """broadcast_event for raw_packet dispatches to fanout broadcast_raw."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_fm.broadcast_raw = AsyncMock()

            broadcast_event("raw_packet", {"data": "ff00"})
            await asyncio.sleep(0)

            mock_ws.broadcast.assert_called_once()
            mock_fm.broadcast_raw.assert_called_once_with({"data": "ff00"})


class TestTypedEventSerialization:
    """Tests for typed websocket event serialization."""

    def test_dump_ws_event_preserves_optional_message_acked_shape(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event("message_acked", {"message_id": 7, "ack_count": 2})

        assert json.loads(serialized) == {
            "type": "message_acked",
            "data": {"message_id": 7, "ack_count": 2},
        }

    def test_dump_ws_event_falls_back_to_raw_payload_when_validation_fails(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event("message_acked", {"ack_count": 2})

        assert json.loads(serialized) == {
            "type": "message_acked",
            "data": {"ack_count": 2},
        }
