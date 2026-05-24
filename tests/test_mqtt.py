"""Tests for MQTT publisher module."""

import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.fanout.mqtt import MqttPublisher, _build_message_topic, _build_raw_packet_topic


def _make_settings(**overrides) -> SimpleNamespace:
    """Create a settings namespace with MQTT fields."""
    defaults = {
        "mqtt_broker_host": "broker.local",
        "mqtt_broker_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "mqtt_use_tls": False,
        "mqtt_tls_insecure": False,
        "mqtt_topic_prefix": "meshcore",
        "mqtt_publish_messages": True,
        "mqtt_publish_raw_packets": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestTopicBuilders:
    def test_dm_message_topic(self):
        topic = _build_message_topic("meshcore", {"type": "PRIV", "conversation_key": "abc123"})
        assert topic == "meshcore/dm:abc123"

    def test_channel_message_topic(self):
        topic = _build_message_topic("meshcore", {"type": "CHAN", "conversation_key": "def456"})
        assert topic == "meshcore/gm:def456"

    def test_unknown_message_type_fallback(self):
        topic = _build_message_topic("meshcore", {"type": "OTHER", "conversation_key": "xyz"})
        assert topic == "meshcore/message:xyz"

    def test_custom_prefix(self):
        topic = _build_message_topic("myprefix", {"type": "PRIV", "conversation_key": "abc"})
        assert topic == "myprefix/dm:abc"

    def test_raw_packet_dm_topic(self):
        data = {"decrypted_info": {"contact_key": "contact123", "channel_key": None}}
        topic = _build_raw_packet_topic("meshcore", data)
        assert topic == "meshcore/raw/dm:contact123"

    def test_raw_packet_gm_topic(self):
        data = {"decrypted_info": {"contact_key": None, "channel_key": "chan456"}}
        topic = _build_raw_packet_topic("meshcore", data)
        assert topic == "meshcore/raw/gm:chan456"

    def test_raw_packet_unrouted_no_info(self):
        data = {"decrypted_info": None}
        topic = _build_raw_packet_topic("meshcore", data)
        assert topic == "meshcore/raw/unrouted"

    def test_raw_packet_unrouted_empty_keys(self):
        data = {"decrypted_info": {"contact_key": None, "channel_key": None}}
        topic = _build_raw_packet_topic("meshcore", data)
        assert topic == "meshcore/raw/unrouted"

    def test_raw_packet_contact_takes_precedence_over_channel(self):
        data = {"decrypted_info": {"contact_key": "c1", "channel_key": "ch1"}}
        topic = _build_raw_packet_topic("meshcore", data)
        assert topic == "meshcore/raw/dm:c1"


class TestMqttPublisher:
    def test_initial_state(self):
        pub = MqttPublisher()
        assert pub.connected is False
        assert pub._client is None

    def test_not_configured_when_host_empty(self):
        pub = MqttPublisher()
        pub._settings = _make_settings(mqtt_broker_host="")
        assert pub._is_configured() is False

    def test_configured_when_host_set(self):
        pub = MqttPublisher()
        pub._settings = _make_settings(mqtt_broker_host="broker.local")
        assert pub._is_configured() is True

    @pytest.mark.asyncio
    async def test_publish_drops_silently_when_disconnected(self):
        pub = MqttPublisher()
        pub.connected = False
        # Should not raise
        await pub.publish("topic", {"key": "value"})

    @pytest.mark.asyncio
    async def test_publish_calls_client_when_connected(self):
        pub = MqttPublisher()
        pub.connected = True
        mock_client = AsyncMock()
        pub._client = mock_client

        await pub.publish("test/topic", {"msg": "hello"})

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "test/topic"

    @pytest.mark.asyncio
    async def test_publish_passes_retain_flag(self):
        pub = MqttPublisher()
        pub.connected = True
        mock_client = AsyncMock()
        pub._client = mock_client

        await pub.publish("test/topic", {"msg": "hello"}, retain=True)

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "test/topic"
        assert call_args[1]["retain"] is True

    @pytest.mark.asyncio
    async def test_publish_retain_defaults_false(self):
        pub = MqttPublisher()
        pub.connected = True
        mock_client = AsyncMock()
        pub._client = mock_client

        await pub.publish("test/topic", {"msg": "hello"})

        call_args = mock_client.publish.call_args
        assert call_args[1]["retain"] is False

    @pytest.mark.asyncio
    async def test_publish_handles_exception_gracefully(self, caplog):
        pub = MqttPublisher()
        pub.set_integration_name("Primary MQTT")
        pub.connected = True
        mock_client = AsyncMock()
        mock_client.publish.side_effect = Exception("Network error")
        pub._client = mock_client

        # Should not raise
        await pub.publish("test/topic", {"msg": "hello"})

        # After a publish failure, connected should be cleared to stop
        # further attempts and reflect accurate status
        assert pub.connected is False
        assert pub.last_error == "Network error"
        assert "Primary MQTT" in caplog.text
        assert "usually transient network noise" in caplog.text

    @pytest.mark.asyncio
    async def test_stop_resets_state(self):
        pub = MqttPublisher()
        pub.connected = True
        pub._client = MagicMock()
        pub._task = None  # No task to cancel

        await pub.stop()

        assert pub.connected is False
        assert pub._client is None


class TestBuildTlsContext:
    def test_returns_none_when_tls_disabled(self):
        settings = _make_settings(mqtt_use_tls=False)
        assert MqttPublisher._build_tls_context(settings) is None

    def test_returns_context_when_tls_enabled(self):
        settings = _make_settings(mqtt_use_tls=True)
        ctx = MqttPublisher._build_tls_context(settings)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_insecure_skips_verification(self):
        settings = _make_settings(mqtt_use_tls=True, mqtt_tls_insecure=True)
        ctx = MqttPublisher._build_tls_context(settings)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


def _mock_aiomqtt_client():
    """Create a mock aiomqtt.Client that works as an async context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestConnectionLoop:
    """Integration tests for MqttPublisher._connection_loop."""

    @pytest.mark.asyncio
    async def test_connects_and_sets_state(self):
        """Connection loop should connect and set connected=True."""
        import asyncio

        pub = MqttPublisher()
        settings = _make_settings()

        mock_client = _mock_aiomqtt_client()

        # The connection loop will block forever in the inner wait loop.
        # We let it connect, verify state, then cancel.
        connected_event = asyncio.Event()

        original_aenter = mock_client.__aenter__

        async def side_effect_aenter(*a, **kw):
            result = await original_aenter(*a, **kw)
            # Signal that connection happened
            connected_event.set()
            return result

        mock_client.__aenter__ = AsyncMock(side_effect=side_effect_aenter)

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", return_value=mock_client),
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_health"),
        ):
            await pub.start(settings)

            # Wait for connection to be established
            await asyncio.wait_for(connected_event.wait(), timeout=2)

            assert pub.connected is True
            assert pub._client is mock_client

            await pub.stop()
            assert pub.connected is False

    @pytest.mark.asyncio
    async def test_reconnects_after_connection_failure(self):
        """Connection loop should retry after a connection error with backoff."""
        import asyncio

        from app.fanout.mqtt_base import _BACKOFF_MIN

        pub = MqttPublisher()
        settings = _make_settings()

        attempt_count = 0
        connected_event = asyncio.Event()

        def make_client_factory():
            """Factory that fails first, succeeds second."""

            def factory(**kwargs):
                nonlocal attempt_count
                attempt_count += 1
                mock = _mock_aiomqtt_client()
                if attempt_count == 1:
                    # First attempt: fail on __aenter__
                    mock.__aenter__ = AsyncMock(side_effect=ConnectionRefusedError("refused"))
                else:
                    # Second attempt: succeed and signal
                    original_aenter = mock.__aenter__

                    async def signal_aenter(*a, **kw):
                        result = await original_aenter(*a, **kw)
                        connected_event.set()
                        return result

                    mock.__aenter__ = AsyncMock(side_effect=signal_aenter)
                return mock

            return factory

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=make_client_factory()),
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
            patch("app.fanout.mqtt_base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            await pub.start(settings)

            # Wait for second (successful) connection
            await asyncio.wait_for(connected_event.wait(), timeout=5)

            assert pub.connected is True
            assert attempt_count == 2
            # Should have slept with initial backoff after first failure
            mock_sleep.assert_called_once_with(_BACKOFF_MIN)

            await pub.stop()

    @pytest.mark.asyncio
    async def test_backoff_increases_on_repeated_failures(self):
        """Backoff should double after each failure, capped at _backoff_max."""
        import asyncio

        from app.fanout.mqtt_base import _BACKOFF_MIN

        pub = MqttPublisher()
        settings = _make_settings()

        max_failures = 4  # enough to observe doubling and capping

        def make_failing_factory():
            call_count = 0

            def factory(**kwargs):
                nonlocal call_count
                call_count += 1
                mock = _mock_aiomqtt_client()
                mock.__aenter__ = AsyncMock(side_effect=OSError("network down"))
                return mock

            return factory, lambda: call_count

        factory, get_count = make_failing_factory()
        sleep_args: list[int] = []

        async def capture_sleep(duration):
            sleep_args.append(duration)
            if len(sleep_args) >= max_failures:
                # Cancel the loop after enough failures
                pub._task.cancel()
                raise asyncio.CancelledError

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=factory),
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
            patch("app.fanout.mqtt_base.asyncio.sleep", side_effect=capture_sleep),
        ):
            await pub.start(settings)
            try:
                await pub._task
            except asyncio.CancelledError:
                pass

        assert sleep_args[0] == _BACKOFF_MIN
        assert sleep_args[1] == _BACKOFF_MIN * 2
        assert sleep_args[2] == _BACKOFF_MIN * 4
        # Fourth is still doubling (5*8=40), not yet at _backoff_max
        assert sleep_args[3] == _BACKOFF_MIN * 8

    @pytest.mark.asyncio
    async def test_waits_for_settings_when_unconfigured(self):
        """When host is empty, loop should block until settings change."""
        import asyncio

        pub = MqttPublisher()
        unconfigured = _make_settings(mqtt_broker_host="")

        connected_event = asyncio.Event()

        def make_success_client(**kwargs):
            mock = _mock_aiomqtt_client()
            original_aenter = mock.__aenter__

            async def signal_aenter(*a, **kw):
                result = await original_aenter(*a, **kw)
                connected_event.set()
                return result

            mock.__aenter__ = AsyncMock(side_effect=signal_aenter)
            return mock

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=make_success_client),
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_health"),
        ):
            # Start with unconfigured settings — loop should wait
            await pub.start(unconfigured)
            await asyncio.sleep(0.05)
            assert pub.connected is False

            # Now provide configured settings — loop should connect
            configured = _make_settings(mqtt_broker_host="broker.local")
            pub._settings = configured
            pub._settings_version += 1
            pub._version_event.set()

            await asyncio.wait_for(connected_event.wait(), timeout=2)
            assert pub.connected is True

            await pub.stop()

    @pytest.mark.asyncio
    async def test_health_broadcast_on_connect_and_failure(self):
        """_broadcast_health should be called on connect and on failure."""
        import asyncio

        pub = MqttPublisher()
        settings = _make_settings()

        health_calls: list[str] = []
        connect_event = asyncio.Event()

        def track_health():
            health_calls.append("health")

        def make_client(**kwargs):
            mock = _mock_aiomqtt_client()
            original_aenter = mock.__aenter__

            async def signal_aenter(*a, **kw):
                result = await original_aenter(*a, **kw)
                connect_event.set()
                return result

            mock.__aenter__ = AsyncMock(side_effect=signal_aenter)
            return mock

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=make_client),
            patch("app.fanout.mqtt_base._broadcast_health", side_effect=track_health),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_health"),
        ):
            await pub.start(settings)
            await asyncio.wait_for(connect_event.wait(), timeout=2)

            # Should have been called once on successful connect
            assert len(health_calls) == 1

            await pub.stop()

    @pytest.mark.asyncio
    async def test_health_broadcast_on_connection_error(self):
        """_broadcast_health should be called when connection fails."""
        import asyncio

        pub = MqttPublisher()
        settings = _make_settings()

        health_calls: list[str] = []

        def track_health():
            health_calls.append("health")

        async def cancel_on_sleep(duration):
            # Cancel after the first backoff sleep to stop the loop
            pub._task.cancel()
            raise asyncio.CancelledError

        def make_failing_client(**kwargs):
            mock = _mock_aiomqtt_client()
            mock.__aenter__ = AsyncMock(side_effect=OSError("refused"))
            return mock

        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=make_failing_client),
            patch("app.fanout.mqtt_base._broadcast_health", side_effect=track_health),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
            patch("app.fanout.mqtt_base.asyncio.sleep", side_effect=cancel_on_sleep),
        ):
            await pub.start(settings)
            try:
                await pub._task
            except asyncio.CancelledError:
                pass

        # Should have been called once on connection failure
        assert len(health_calls) == 1
