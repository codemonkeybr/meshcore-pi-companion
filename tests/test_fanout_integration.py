"""Integration tests: real MQTT capture broker + real fanout modules.

Spins up a minimal in-process MQTT 3.1.1 broker on a random port, creates
fanout configs in an in-memory DB, starts real MqttPrivateModule instances
via the FanoutManager, and verifies that PUBLISH packets arrive (or don't)
based on enabled/disabled state and scope settings.

Also covers webhook and Apprise modules with real HTTP capture servers.
"""

import asyncio
import json
import platform
import struct

import pytest

import app.repository.fanout as fanout_mod

# paho-mqtt's aiomqtt transport uses add_reader/add_writer which are not
# supported on Windows' default ProactorEventLoop.  These integration tests
# require a SelectorEventLoop, so skip the entire module on Windows.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="MQTT integration tests require SelectorEventLoop (not available on Windows)",
)
from app.database import Database
from app.fanout.manager import FanoutManager
from app.repository.fanout import FanoutConfigRepository

# ---------------------------------------------------------------------------
# Minimal async MQTT 3.1.1 capture broker
# ---------------------------------------------------------------------------


class MqttCaptureBroker:
    """Tiny TCP server that speaks just enough MQTT to capture PUBLISH packets."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self._server: asyncio.Server | None = None
        self.port: int = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for(self, count: int, timeout: float = 5.0) -> list[tuple[str, dict]]:
        """Block until *count* messages captured, or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.published) < count:
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.02)
        return list(self.published)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                first = await reader.readexactly(1)
                pkt_type = (first[0] & 0xF0) >> 4
                rem_len = await self._read_varlen(reader)
                payload = await reader.readexactly(rem_len) if rem_len else b""

                if pkt_type == 1:  # CONNECT -> CONNACK
                    writer.write(b"\x20\x02\x00\x00")
                    await writer.drain()
                elif pkt_type == 3:  # PUBLISH (QoS 0)
                    topic_len = struct.unpack("!H", payload[:2])[0]
                    topic = payload[2 : 2 + topic_len].decode()
                    body = payload[2 + topic_len :]
                    try:
                        data = json.loads(body)
                    except Exception:
                        data = {}
                    self.published.append((topic, data))
                elif pkt_type == 12:  # PINGREQ -> PINGRESP
                    writer.write(b"\xd0\x00")
                    await writer.drain()
                elif pkt_type == 14:  # DISCONNECT
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    @staticmethod
    async def _read_varlen(reader: asyncio.StreamReader) -> int:
        value, shift = 0, 0
        while True:
            b = (await reader.readexactly(1))[0]
            value |= (b & 0x7F) << shift
            if not (b & 0x80):
                return value
            shift += 7


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mqtt_broker():
    broker = MqttCaptureBroker()
    await broker.start()
    yield broker
    await broker.stop()


@pytest.fixture
async def integration_db():
    """In-memory DB with fanout_configs, wired into the repository module.

    Database.connect() runs all migrations which create the fanout_configs
    table, so no manual DDL is needed here.
    """
    test_db = Database(":memory:")
    await test_db.connect()

    original_db = fanout_mod.db
    fanout_mod.db = test_db
    try:
        yield test_db
    finally:
        fanout_mod.db = original_db
        await test_db.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_connected(manager: FanoutManager, config_id: str, timeout: float = 5.0):
    """Poll until the module reports 'connected'."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        entry = manager._modules.get(config_id)
        if entry and entry[0].status == "connected":
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Module {config_id} did not connect within {timeout}s")


def _private_config(port: int, prefix: str) -> dict:
    return {"broker_host": "127.0.0.1", "broker_port": port, "topic_prefix": prefix}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFanoutMqttIntegration:
    """End-to-end: real capture broker <-> real fanout modules."""

    @pytest.mark.asyncio
    async def test_both_enabled_both_receive(self, mqtt_broker, integration_db):
        """Two enabled integrations with different prefixes both receive messages."""
        from unittest.mock import patch

        cfg_a = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Alpha",
            config=_private_config(mqtt_broker.port, "alpha"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )
        cfg_b = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Beta",
            config=_private_config(mqtt_broker.port, "beta"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg_a["id"])
                await _wait_connected(manager, cfg_b["id"])

                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "hello"}
                )

                messages = await mqtt_broker.wait_for(2)
            finally:
                await manager.stop_all()

        topics = {m[0] for m in messages}
        assert "alpha/dm:pk1" in topics
        assert "beta/dm:pk1" in topics

    @pytest.mark.asyncio
    async def test_private_mqtt_preserves_full_message_payload(self, mqtt_broker, integration_db):
        """Private MQTT publishes the full message payload without dropping fields."""
        from unittest.mock import patch

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Full Payload",
            config=_private_config(mqtt_broker.port, "mesh"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )

        payload = {
            "type": "CHAN",
            "conversation_key": "ch1",
            "channel_name": "#general",
            "text": "Alice: hello mqtt",
            "sender_name": "Alice",
            "sender_key": "ab" * 32,
            "sender_timestamp": 1700000000,
            "received_at": 1700000001,
            "paths": [{"path": "aabb", "received_at": 1700000001}],
            "outgoing": False,
            "acked": 2,
        }

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg["id"])

                await manager.broadcast_message(payload)
                messages = await mqtt_broker.wait_for(1)
            finally:
                await manager.stop_all()

        assert len(messages) == 1
        topic, body = messages[0]
        assert topic == "mesh/gm:ch1"
        assert body == payload

    @pytest.mark.asyncio
    async def test_one_disabled_only_enabled_receives(self, mqtt_broker, integration_db):
        """Disabled integration must not publish any messages."""
        from unittest.mock import patch

        cfg_on = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Enabled",
            config=_private_config(mqtt_broker.port, "on"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )
        await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Disabled",
            config=_private_config(mqtt_broker.port, "off"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=False,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg_on["id"])

                # Only 1 module should be loaded
                assert len(manager._modules) == 1

                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "hello"}
                )

                await mqtt_broker.wait_for(1)
                await asyncio.sleep(0.2)  # extra time to catch stray messages
            finally:
                await manager.stop_all()

        assert len(mqtt_broker.published) == 1
        assert mqtt_broker.published[0][0] == "on/dm:pk1"

    @pytest.mark.asyncio
    async def test_both_disabled_nothing_published(self, mqtt_broker, integration_db):
        """Both disabled -> zero messages published."""
        from unittest.mock import patch

        await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="A",
            config=_private_config(mqtt_broker.port, "a"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=False,
        )
        await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="B",
            config=_private_config(mqtt_broker.port, "b"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=False,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                assert len(manager._modules) == 0

                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "hello"}
                )
                await asyncio.sleep(0.3)
            finally:
                await manager.stop_all()

        assert len(mqtt_broker.published) == 0

    @pytest.mark.asyncio
    async def test_disable_after_enable_stops_publishing(self, mqtt_broker, integration_db):
        """Disabling a live integration stops its publishing immediately."""
        from unittest.mock import patch

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Toggle",
            config=_private_config(mqtt_broker.port, "toggle"),
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg["id"])

                # Publishes while enabled
                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "msg1"}
                )
                await mqtt_broker.wait_for(1)
                assert len(mqtt_broker.published) == 1

                # Disable via DB + reload
                await FanoutConfigRepository.update(cfg["id"], enabled=False)
                await manager.reload_config(cfg["id"])
                assert cfg["id"] not in manager._modules

                # Should NOT publish after disable
                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk2", "text": "msg2"}
                )
                await asyncio.sleep(0.3)
            finally:
                await manager.stop_all()

        # Only the first message
        assert len(mqtt_broker.published) == 1
        assert mqtt_broker.published[0][0] == "toggle/dm:pk1"

    @pytest.mark.asyncio
    async def test_scope_messages_only_no_raw(self, mqtt_broker, integration_db):
        """Module with raw_packets=none receives messages but not raw packets."""
        from unittest.mock import patch

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Messages Only",
            config=_private_config(mqtt_broker.port, "msgsonly"),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg["id"])

                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "hi"}
                )
                await manager.broadcast_raw({"data": "aabbccdd"})

                await mqtt_broker.wait_for(1)
                await asyncio.sleep(0.2)
            finally:
                await manager.stop_all()

        assert len(mqtt_broker.published) == 1
        assert "dm:pk1" in mqtt_broker.published[0][0]

    @pytest.mark.asyncio
    async def test_scope_raw_only_no_messages(self, mqtt_broker, integration_db):
        """Module with messages=none receives raw packets but not decoded messages."""
        from unittest.mock import patch

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Raw Only",
            config=_private_config(mqtt_broker.port, "rawonly"),
            scope={"messages": "none", "raw_packets": "all"},
            enabled=True,
        )

        manager = FanoutManager()
        with (
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_error"),
            patch("app.websocket.broadcast_health"),
        ):
            try:
                await manager.load_from_db()
                await _wait_connected(manager, cfg["id"])

                await manager.broadcast_message(
                    {"type": "PRIV", "conversation_key": "pk1", "text": "hi"}
                )
                await manager.broadcast_raw({"data": "aabbccdd"})

                await mqtt_broker.wait_for(1)
                await asyncio.sleep(0.2)
            finally:
                await manager.stop_all()

        assert len(mqtt_broker.published) == 1
        assert "raw/" in mqtt_broker.published[0][0]


# ---------------------------------------------------------------------------
# Webhook capture HTTP server
# ---------------------------------------------------------------------------


class WebhookCaptureServer:
    """Tiny HTTP server that captures POST requests for webhook testing."""

    def __init__(self):
        self.received: list[dict] = []
        self._server: asyncio.Server | None = None
        self.port: int = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for(self, count: int, timeout: float = 5.0) -> list[dict]:
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.received) < count:
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.02)
        return list(self.received)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # Read HTTP request line
            request_line = await reader.readline()
            if not request_line:
                return

            # Read headers
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    key, val = decoded.split(":", 1)
                    headers[key.strip().lower()] = val.strip()

            # Read body
            content_length = int(headers.get("content-length", "0"))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            payload: dict = {}
            if body:
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {"_raw": body.decode("utf-8", errors="replace")}

            self.received.append(
                {
                    "method": request_line.decode().split()[0],
                    "headers": headers,
                    "body": payload,
                }
            )

            # Send 200 OK
            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
            writer.write(response)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def webhook_server():
    server = WebhookCaptureServer()
    await server.start()
    yield server
    await server.stop()


def _webhook_config(port: int, extra_headers: dict | None = None) -> dict:
    return {
        "url": f"http://127.0.0.1:{port}/hook",
        "method": "POST",
        "headers": extra_headers or {},
    }


# ---------------------------------------------------------------------------
# Webhook integration tests
# ---------------------------------------------------------------------------


class TestFanoutWebhookIntegration:
    """End-to-end: real HTTP capture server <-> real WebhookModule."""

    @pytest.mark.asyncio
    async def test_webhook_receives_message(self, webhook_server, integration_db):
        """An enabled webhook receives message data via HTTP POST."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Test Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "hello webhook"}
            )

            results = await webhook_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) == 1
        assert results[0]["body"]["text"] == "hello webhook"
        assert results[0]["body"]["conversation_key"] == "pk1"
        assert results[0]["headers"].get("x-webhook-event") == "message"

    @pytest.mark.asyncio
    async def test_webhook_sends_custom_headers(self, webhook_server, integration_db):
        """Webhook forwards custom headers from config."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Custom Header Hook",
            config=_webhook_config(webhook_server.port, extra_headers={"X-Custom": "my-value"}),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch1", "text": "header test"}
            )

            results = await webhook_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) == 1
        assert results[0]["headers"].get("x-custom") == "my-value"

    @pytest.mark.asyncio
    async def test_webhook_preserves_full_message_payload(self, webhook_server, integration_db):
        """Webhook delivers the full message payload body without dropping fields."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Full Payload Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        payload = {
            "type": "CHAN",
            "conversation_key": "ch1",
            "channel_name": "#general",
            "text": "Alice: hello webhook",
            "sender_name": "Alice",
            "sender_key": "ab" * 32,
            "sender_timestamp": 1700000000,
            "received_at": 1700000001,
            "paths": [{"path": "aabb", "received_at": 1700000001}],
            "outgoing": False,
            "acked": 2,
        }

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(payload)
            results = await webhook_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) == 1
        assert results[0]["body"] == payload

    @pytest.mark.asyncio
    async def test_webhook_hmac_signature(self, webhook_server, integration_db):
        """Webhook sends HMAC-SHA256 signature when hmac_secret is configured."""
        import hashlib
        import hmac
        import json

        secret = "test-secret-key"
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="HMAC Hook",
            config={
                **_webhook_config(webhook_server.port),
                "hmac_secret": secret,
                "hmac_header": "X-My-Sig",
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            msg = {"type": "CHAN", "conversation_key": "ch1", "text": "hmac test"}
            await manager.broadcast_message(msg)

            results = await webhook_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) == 1
        sig_header = results[0]["headers"].get("x-my-sig", "")
        assert sig_header.startswith("sha256=")

        # Verify the signature matches
        body_bytes = json.dumps(results[0]["body"], separators=(",", ":"), sort_keys=True).encode()
        expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected}"

    @pytest.mark.asyncio
    async def test_webhook_disabled_no_delivery(self, webhook_server, integration_db):
        """Disabled webhook should not deliver any messages."""
        await FanoutConfigRepository.create(
            config_type="webhook",
            name="Disabled Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=False,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert len(manager._modules) == 0

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "nope"}
            )
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 0

    @pytest.mark.asyncio
    async def test_webhook_scope_selective_channels(self, webhook_server, integration_db):
        """Webhook with selective scope only fires for matching channels."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Selective Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": {"channels": ["ch-yes"], "contacts": "none"}, "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            # Matching channel — should deliver
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-yes", "text": "included"}
            )
            # Non-matching channel — should NOT deliver
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-no", "text": "excluded"}
            )
            # DM — contacts is "none", should NOT deliver
            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "dm excluded"}
            )

            await webhook_server.wait_for(1)
            await asyncio.sleep(0.3)  # wait for any stragglers
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 1
        assert webhook_server.received[0]["body"]["text"] == "included"

    @pytest.mark.asyncio
    async def test_webhook_scope_selective_contacts(self, webhook_server, integration_db):
        """Webhook with selective scope only fires for matching contacts."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Contact Hook",
            config=_webhook_config(webhook_server.port),
            scope={
                "messages": {"channels": "none", "contacts": ["pk-yes"]},
                "raw_packets": "none",
            },
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk-yes", "text": "dm included"}
            )
            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk-no", "text": "dm excluded"}
            )

            await webhook_server.wait_for(1)
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 1
        assert webhook_server.received[0]["body"]["text"] == "dm included"

    @pytest.mark.asyncio
    async def test_webhook_scope_all_receives_everything(self, webhook_server, integration_db):
        """Webhook with scope messages='all' receives DMs and channel messages."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="All Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch1", "text": "channel msg"}
            )
            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "dm msg"}
            )

            results = await webhook_server.wait_for(2)
        finally:
            await manager.stop_all()

        assert len(results) == 2
        texts = {r["body"]["text"] for r in results}
        assert "channel msg" in texts
        assert "dm msg" in texts

    @pytest.mark.asyncio
    async def test_webhook_scope_none_receives_nothing(self, webhook_server, integration_db):
        """Webhook with scope messages='none' receives nothing."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="None Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "none", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "should not arrive"}
            )
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 0

    @pytest.mark.asyncio
    async def test_two_webhooks_both_receive(self, webhook_server, integration_db):
        """Two enabled webhooks both receive the same message."""
        cfg_a = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Hook A",
            config=_webhook_config(webhook_server.port, extra_headers={"X-Hook-Id": "a"}),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )
        cfg_b = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Hook B",
            config=_webhook_config(webhook_server.port, extra_headers={"X-Hook-Id": "b"}),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg_a["id"])
            await _wait_connected(manager, cfg_b["id"])

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "multi"}
            )

            results = await webhook_server.wait_for(2)
        finally:
            await manager.stop_all()

        assert len(results) == 2
        hook_ids = {r["headers"].get("x-hook-id") for r in results}
        assert "a" in hook_ids
        assert "b" in hook_ids

    @pytest.mark.asyncio
    async def test_two_webhooks_with_different_channel_scopes_receive_only_matches(
        self, webhook_server, integration_db
    ):
        """Scoped webhooks run in parallel and each receive only their selected room."""
        cfg_a = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Hook A",
            config=_webhook_config(webhook_server.port, extra_headers={"X-Hook-Id": "a"}),
            scope={"messages": {"channels": ["ch-a"], "contacts": "none"}, "raw_packets": "none"},
            enabled=True,
        )
        cfg_b = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Hook B",
            config=_webhook_config(webhook_server.port, extra_headers={"X-Hook-Id": "b"}),
            scope={"messages": {"channels": ["ch-b"], "contacts": "none"}, "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg_a["id"])
            await _wait_connected(manager, cfg_b["id"])

            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-a", "text": "room a"}
            )
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-b", "text": "room b"}
            )
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-c", "text": "room c"}
            )

            results = await webhook_server.wait_for(2)
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(results) == 2
        seen = {(r["headers"].get("x-hook-id"), r["body"]["conversation_key"]) for r in results}
        assert ("a", "ch-a") in seen
        assert ("b", "ch-b") in seen

    @pytest.mark.asyncio
    async def test_fifty_webhooks_same_target_all_deliver(self, webhook_server, integration_db):
        """A large number of webhook modules targeting one endpoint all deliver."""
        config_ids: list[str] = []
        webhook_count = 50

        for i in range(webhook_count):
            cfg = await FanoutConfigRepository.create(
                config_type="webhook",
                name=f"Hook {i}",
                config=_webhook_config(webhook_server.port, extra_headers={"X-Hook-Id": str(i)}),
                scope={"messages": "all", "raw_packets": "none"},
                enabled=True,
            )
            config_ids.append(cfg["id"])

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            for config_id in config_ids:
                await _wait_connected(manager, config_id)

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "fanout storm"}
            )

            results = await webhook_server.wait_for(webhook_count, timeout=10.0)
            await asyncio.sleep(0.5)
        finally:
            await manager.stop_all()

        assert len(results) == webhook_count
        hook_ids = {r["headers"].get("x-hook-id") for r in results}
        assert hook_ids == {str(i) for i in range(webhook_count)}
        assert all(r["body"]["text"] == "fanout storm" for r in results)

    @pytest.mark.asyncio
    async def test_webhook_disable_stops_delivery(self, webhook_server, integration_db):
        """Disabling a webhook stops delivery immediately."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Toggle Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "before disable"}
            )
            await webhook_server.wait_for(1)
            assert len(webhook_server.received) == 1

            # Disable
            await FanoutConfigRepository.update(cfg["id"], enabled=False)
            await manager.reload_config(cfg["id"])
            assert cfg["id"] not in manager._modules

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk2", "text": "after disable"}
            )
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 1

    @pytest.mark.asyncio
    async def test_webhook_scope_except_channels(self, webhook_server, integration_db):
        """Webhook with except-mode excludes listed channels, includes others."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Except Hook",
            config=_webhook_config(webhook_server.port),
            scope={
                "messages": {
                    "channels": {"except": ["ch-excluded"]},
                    "contacts": {"except": []},
                },
                "raw_packets": "none",
            },
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            # Excluded channel — should NOT deliver
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-excluded", "text": "nope"}
            )
            # Non-excluded channel — should deliver
            await manager.broadcast_message(
                {"type": "CHAN", "conversation_key": "ch-other", "text": "yes"}
            )
            # DM with empty except list — should deliver
            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "dm yes"}
            )

            await webhook_server.wait_for(2)
            await asyncio.sleep(0.3)
        finally:
            await manager.stop_all()

        assert len(webhook_server.received) == 2
        texts = {r["body"]["text"] for r in webhook_server.received}
        assert "yes" in texts
        assert "dm yes" in texts
        assert "nope" not in texts

    @pytest.mark.asyncio
    async def test_webhook_delivers_outgoing_messages(self, webhook_server, integration_db):
        """Webhooks should deliver outgoing messages (unlike Apprise which skips them)."""
        cfg = await FanoutConfigRepository.create(
            config_type="webhook",
            name="Outgoing Hook",
            config=_webhook_config(webhook_server.port),
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            await _wait_connected(manager, cfg["id"])

            await manager.broadcast_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "outgoing msg",
                    "outgoing": True,
                }
            )

            results = await webhook_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) == 1
        assert results[0]["body"]["text"] == "outgoing msg"
        assert results[0]["body"]["outgoing"] is True


# ---------------------------------------------------------------------------
# Apprise integration tests (real HTTP capture server + real AppriseModule)
# ---------------------------------------------------------------------------


class AppriseJsonCaptureServer:
    """Minimal HTTP server that captures JSON POSTs from Apprise's json:// plugin.

    Apprise json:// sends POST with JSON body containing title, body, type fields.
    """

    def __init__(self):
        self.received: list[dict] = []
        self._server: asyncio.Server | None = None
        self.port: int = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for(self, count: int, timeout: float = 10.0) -> list[dict]:
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.received) < count:
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.05)
        return list(self.received)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await reader.readline()
            if not request_line:
                return

            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    key, val = decoded.split(":", 1)
                    headers[key.strip().lower()] = val.strip()

            content_length = int(headers.get("content-length", "0"))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            if body:
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {"_raw": body.decode("utf-8", errors="replace")}
                self.received.append(payload)

            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
            writer.write(response)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def apprise_capture_server():
    server = AppriseJsonCaptureServer()
    await server.start()
    yield server
    await server.stop()


class TestFanoutAppriseIntegration:
    """End-to-end: real HTTP capture server <-> real AppriseModule via json:// URL."""

    @pytest.mark.asyncio
    async def test_apprise_delivers_incoming_dm(self, apprise_capture_server, integration_db):
        """Apprise module delivers incoming DMs via json:// to a real HTTP server."""
        cfg = await FanoutConfigRepository.create(
            config_type="apprise",
            name="Test Apprise",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
                "preserve_identity": True,
                "include_path": False,
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert cfg["id"] in manager._modules

            await manager.broadcast_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "hello from mesh",
                    "sender_name": "Alice",
                    "outgoing": False,
                }
            )

            results = await apprise_capture_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) >= 1
        # Apprise json:// sends body field with the formatted message
        body_text = str(results[0])
        assert "Alice" in body_text
        assert "hello from mesh" in body_text

    @pytest.mark.asyncio
    async def test_apprise_delivers_incoming_channel_msg(
        self, apprise_capture_server, integration_db
    ):
        """Apprise module delivers incoming channel messages."""
        cfg = await FanoutConfigRepository.create(
            config_type="apprise",
            name="Channel Apprise",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
                "include_path": False,
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert cfg["id"] in manager._modules

            await manager.broadcast_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch1",
                    "channel_name": "#general",
                    "text": "channel hello",
                    "sender_name": "Bob",
                    "outgoing": False,
                }
            )

            results = await apprise_capture_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) >= 1
        body_text = str(results[0])
        assert "Bob" in body_text
        assert "channel hello" in body_text
        assert "#general" in body_text

    @pytest.mark.asyncio
    async def test_apprise_skips_outgoing(self, apprise_capture_server, integration_db):
        """Apprise should NOT deliver outgoing messages."""
        cfg = await FanoutConfigRepository.create(
            config_type="apprise",
            name="No Outgoing",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert cfg["id"] in manager._modules

            await manager.broadcast_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "my outgoing",
                    "sender_name": "Me",
                    "outgoing": True,
                }
            )

            await asyncio.sleep(1.0)
        finally:
            await manager.stop_all()

        assert len(apprise_capture_server.received) == 0

    @pytest.mark.asyncio
    async def test_apprise_disabled_no_delivery(self, apprise_capture_server, integration_db):
        """Disabled Apprise module should not deliver anything."""
        await FanoutConfigRepository.create(
            config_type="apprise",
            name="Disabled Apprise",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=False,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert len(manager._modules) == 0

            await manager.broadcast_message(
                {"type": "PRIV", "conversation_key": "pk1", "text": "nope"}
            )
            await asyncio.sleep(0.5)
        finally:
            await manager.stop_all()

        assert len(apprise_capture_server.received) == 0

    @pytest.mark.asyncio
    async def test_apprise_scope_selective_channels(self, apprise_capture_server, integration_db):
        """Apprise with selective channel scope only delivers matching channels."""
        cfg = await FanoutConfigRepository.create(
            config_type="apprise",
            name="Selective Apprise",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
                "include_path": False,
            },
            scope={
                "messages": {"channels": ["ch-yes"], "contacts": "none"},
                "raw_packets": "none",
            },
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert cfg["id"] in manager._modules

            # Matching channel
            await manager.broadcast_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch-yes",
                    "channel_name": "#yes",
                    "text": "included",
                    "sender_name": "A",
                }
            )
            # Non-matching channel
            await manager.broadcast_message(
                {
                    "type": "CHAN",
                    "conversation_key": "ch-no",
                    "channel_name": "#no",
                    "text": "excluded",
                    "sender_name": "B",
                }
            )
            # DM — contacts is "none"
            await manager.broadcast_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "dm excluded",
                    "sender_name": "C",
                }
            )

            await apprise_capture_server.wait_for(1)
            await asyncio.sleep(1.0)
        finally:
            await manager.stop_all()

        assert len(apprise_capture_server.received) == 1
        body_text = str(apprise_capture_server.received[0])
        assert "included" in body_text

    @pytest.mark.asyncio
    async def test_two_apprise_modules_with_different_channel_scopes_receive_only_matches(
        self, integration_db
    ):
        """Scoped Apprise modules run in parallel and each receive only their selected room."""
        server_a = AppriseJsonCaptureServer()
        server_b = AppriseJsonCaptureServer()
        await server_a.start()
        await server_b.start()
        try:
            cfg_a = await FanoutConfigRepository.create(
                config_type="apprise",
                name="Apprise A",
                config={
                    "urls": f"json://127.0.0.1:{server_a.port}",
                    "include_path": False,
                },
                scope={
                    "messages": {"channels": ["ch-a"], "contacts": "none"},
                    "raw_packets": "none",
                },
                enabled=True,
            )
            cfg_b = await FanoutConfigRepository.create(
                config_type="apprise",
                name="Apprise B",
                config={
                    "urls": f"json://127.0.0.1:{server_b.port}",
                    "include_path": False,
                },
                scope={
                    "messages": {"channels": ["ch-b"], "contacts": "none"},
                    "raw_packets": "none",
                },
                enabled=True,
            )

            manager = FanoutManager()
            try:
                await manager.load_from_db()
                assert cfg_a["id"] in manager._modules
                assert cfg_b["id"] in manager._modules

                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-a",
                        "channel_name": "#a",
                        "text": "room a",
                        "sender_name": "Alice",
                    }
                )
                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-b",
                        "channel_name": "#b",
                        "text": "room b",
                        "sender_name": "Bob",
                    }
                )
                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-c",
                        "channel_name": "#c",
                        "text": "room c",
                        "sender_name": "Carol",
                    }
                )

                results_a = await server_a.wait_for(1)
                results_b = await server_b.wait_for(1)
                await asyncio.sleep(1.0)
            finally:
                await manager.stop_all()

            assert len(results_a) == 1
            assert len(results_b) == 1
            assert "#a" in str(results_a[0])
            assert "room a" in str(results_a[0])
            assert "#b" in str(results_b[0])
            assert "room b" in str(results_b[0])
        finally:
            await server_a.stop()
            await server_b.stop()

    @pytest.mark.asyncio
    async def test_webhook_and_apprise_with_different_channel_scopes_receive_only_matches(
        self, integration_db
    ):
        """Webhook and Apprise dispatch in parallel and each honor their own room scope."""
        webhook = WebhookCaptureServer()
        apprise = AppriseJsonCaptureServer()
        await webhook.start()
        await apprise.start()
        try:
            webhook_cfg = await FanoutConfigRepository.create(
                config_type="webhook",
                name="Room A Hook",
                config=_webhook_config(webhook.port),
                scope={
                    "messages": {"channels": ["ch-a"], "contacts": "none"},
                    "raw_packets": "none",
                },
                enabled=True,
            )
            apprise_cfg = await FanoutConfigRepository.create(
                config_type="apprise",
                name="Room B Apprise",
                config={
                    "urls": f"json://127.0.0.1:{apprise.port}",
                    "include_path": False,
                },
                scope={
                    "messages": {"channels": ["ch-b"], "contacts": "none"},
                    "raw_packets": "none",
                },
                enabled=True,
            )

            manager = FanoutManager()
            try:
                await manager.load_from_db()
                await _wait_connected(manager, webhook_cfg["id"])
                assert apprise_cfg["id"] in manager._modules

                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-a",
                        "channel_name": "#a",
                        "text": "room a",
                        "sender_name": "Alice",
                    }
                )
                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-b",
                        "channel_name": "#b",
                        "text": "room b",
                        "sender_name": "Bob",
                    }
                )
                await manager.broadcast_message(
                    {
                        "type": "CHAN",
                        "conversation_key": "ch-c",
                        "channel_name": "#c",
                        "text": "room c",
                        "sender_name": "Carol",
                    }
                )

                webhook_results = await webhook.wait_for(1)
                apprise_results = await apprise.wait_for(1)
                await asyncio.sleep(1.0)
            finally:
                await manager.stop_all()

            assert len(webhook_results) == 1
            assert webhook_results[0]["body"]["conversation_key"] == "ch-a"
            assert webhook_results[0]["body"]["text"] == "room a"

            assert len(apprise_results) == 1
            apprise_body = str(apprise_results[0])
            assert "#b" in apprise_body
            assert "room b" in apprise_body
        finally:
            await webhook.stop()
            await apprise.stop()

    @pytest.mark.asyncio
    async def test_apprise_includes_routing_path(self, apprise_capture_server, integration_db):
        """Apprise with include_path=True shows routing hops in the body."""
        cfg = await FanoutConfigRepository.create(
            config_type="apprise",
            name="Path Apprise",
            config={
                "urls": f"json://127.0.0.1:{apprise_capture_server.port}",
                "include_path": True,
            },
            scope={"messages": "all", "raw_packets": "none"},
            enabled=True,
        )

        manager = FanoutManager()
        try:
            await manager.load_from_db()
            assert cfg["id"] in manager._modules

            await manager.broadcast_message(
                {
                    "type": "PRIV",
                    "conversation_key": "pk1",
                    "text": "routed msg",
                    "sender_name": "Eve",
                    "paths": [{"path": "2a3b"}],
                }
            )

            results = await apprise_capture_server.wait_for(1)
        finally:
            await manager.stop_all()

        assert len(results) >= 1
        body_text = str(results[0])
        assert "Eve" in body_text
        assert "routed msg" in body_text


# ---------------------------------------------------------------------------
# Bot lifecycle tests
# ---------------------------------------------------------------------------


class TestBotModuleLifecycle:
    """BotModule.stop() must cancel in-flight tasks and prevent response delivery."""

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_tasks(self):
        """Stopping a bot module cancels tasks still in the settle delay."""
        from app.fanout.bot import BotModule

        mod = BotModule("bot1", {"code": "def bot(**k): return 'hi'"}, name="Test Bot")
        mod._active = True

        # Fire off a message — it will enter the 2s settle sleep
        await mod.on_message(
            {"type": "PRIV", "conversation_key": "abc123", "text": "hello", "outgoing": False}
        )
        assert len(mod._tasks) == 1

        # Stop immediately — should cancel the pending task
        await mod.stop()

        assert mod._active is False
        assert len(mod._tasks) == 0

    @pytest.mark.asyncio
    async def test_stop_prevents_response_delivery(self):
        """Even if bot code returns a response, stop() prevents it from being sent."""
        from unittest.mock import AsyncMock, patch

        from app.fanout.bot import BotModule

        mod = BotModule("bot1", {"code": "def bot(**k): return 'reply'"}, name="Test Bot")

        mock_process = AsyncMock()
        with patch("app.fanout.bot.asyncio.sleep", new_callable=AsyncMock):
            # Manually run the handler with _active=True, then set _active=False
            # before process_bot_response would be called
            original_run = mod._run_for_message

            async def run_then_deactivate(data):
                # Deactivate mid-flight by stopping
                mod._active = False
                await original_run(data)

            with patch.object(mod, "_run_for_message", run_then_deactivate):
                await mod.on_message(
                    {
                        "type": "PRIV",
                        "conversation_key": "abc123",
                        "text": "hello",
                        "outgoing": False,
                    }
                )
                # Wait for the task to finish
                if mod._tasks:
                    await asyncio.gather(*mod._tasks, return_exceptions=True)

        # process_bot_response should never have been called
        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_tasks_all_cancelled(self):
        """Multiple in-flight tasks are all cancelled on stop."""
        from app.fanout.bot import BotModule

        mod = BotModule("bot1", {"code": "def bot(**k): return 'hi'"}, name="Test Bot")
        mod._active = True

        # Fire off several messages
        for i in range(5):
            await mod.on_message(
                {
                    "type": "PRIV",
                    "conversation_key": f"key{i}",
                    "text": f"msg{i}",
                    "outgoing": False,
                }
            )
        assert len(mod._tasks) == 5

        await mod.stop()

        assert mod._active is False
        assert len(mod._tasks) == 0


# ---------------------------------------------------------------------------
# Manager restart failure tests
# ---------------------------------------------------------------------------


class TestManagerRestartFailure:
    """_restart_module removes dead module from dispatch table on failure."""

    @pytest.mark.asyncio
    async def test_failed_restart_removes_module(self):
        """When module.start() fails during restart, the module is removed from _modules."""

        from app.fanout.base import FanoutModule

        class FailingModule(FanoutModule):
            def __init__(self):
                super().__init__("fail1", {}, name="Failer")
                self.stop_called = False
                self.start_calls = 0

            async def stop(self):
                self.stop_called = True

            async def start(self):
                self.start_calls += 1
                raise ConnectionError("broker down")

            @property
            def status(self):
                return "error"

        manager = FanoutManager()
        mod = FailingModule()
        manager._modules["fail1"] = (mod, {"messages": "all", "raw_packets": "none"})

        # Restart should catch the error and remove the module
        await manager._restart_module("fail1", mod)

        assert mod.stop_called
        assert mod.start_calls == 1
        assert "fail1" not in manager._modules

    @pytest.mark.asyncio
    async def test_successful_restart_keeps_module(self):
        """When restart succeeds, the module stays in _modules."""
        from app.fanout.base import FanoutModule

        class GoodModule(FanoutModule):
            def __init__(self):
                super().__init__("good1", {}, name="Goodie")

            async def stop(self):
                pass

            async def start(self):
                pass

            @property
            def status(self):
                return "connected"

        manager = FanoutManager()
        mod = GoodModule()
        scope = {"messages": "all", "raw_packets": "none"}
        manager._modules["good1"] = (mod, scope)

        await manager._restart_module("good1", mod)

        assert "good1" in manager._modules

    @pytest.mark.asyncio
    async def test_dead_module_not_dispatched_after_failed_restart(self):
        """After failed restart, the dead module does not receive further dispatches."""
        from app.fanout.base import FanoutModule

        class TrackingModule(FanoutModule):
            def __init__(self, config_id):
                super().__init__(config_id, {}, name=config_id)
                self.messages_received = []

            async def start(self):
                raise RuntimeError("can't start")

            async def stop(self):
                pass

            async def on_message(self, data):
                self.messages_received.append(data)

            @property
            def status(self):
                return "error"

        class HealthyModule(FanoutModule):
            def __init__(self):
                super().__init__("healthy", {}, name="Healthy")
                self.messages_received = []

            async def on_message(self, data):
                self.messages_received.append(data)

            @property
            def status(self):
                return "connected"

        manager = FanoutManager()
        dead = TrackingModule("dead1")
        healthy = HealthyModule()

        scope = {"messages": "all", "raw_packets": "none"}
        manager._modules["dead1"] = (dead, scope)
        manager._modules["healthy"] = (healthy, scope)

        # Simulate failed restart of dead module
        await manager._restart_module("dead1", dead)
        assert "dead1" not in manager._modules

        # Now broadcast — only the healthy module should receive
        await manager.broadcast_message({"type": "PRIV", "conversation_key": "k1", "text": "hi"})

        assert len(healthy.messages_received) == 1
        assert len(dead.messages_received) == 0
