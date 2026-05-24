"""Tests for fanout bus: manager, scope matching, repository, and modules."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import Database
from app.fanout.base import FanoutModule
from app.fanout.manager import (
    _DISPATCH_TIMEOUT_SECONDS,
    FanoutManager,
    _scope_matches_message,
    _scope_matches_raw,
)

# ---------------------------------------------------------------------------
# Scope matching unit tests
# ---------------------------------------------------------------------------


class TestScopeMatchesMessage:
    def test_all_matches_everything(self):
        assert _scope_matches_message({"messages": "all"}, {"type": "PRIV"})

    def test_none_matches_nothing(self):
        assert not _scope_matches_message({"messages": "none"}, {"type": "PRIV"})

    def test_missing_key_defaults_none(self):
        assert not _scope_matches_message({}, {"type": "PRIV"})

    def test_dict_channels_all(self):
        scope = {"messages": {"channels": "all", "contacts": "none"}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})

    def test_dict_channels_none(self):
        scope = {"messages": {"channels": "none"}}
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})

    def test_dict_channels_list_match(self):
        scope = {"messages": {"channels": ["ch1", "ch2"]}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})

    def test_dict_channels_list_no_match(self):
        scope = {"messages": {"channels": ["ch1", "ch2"]}}
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch3"})

    def test_dict_contacts_all(self):
        scope = {"messages": {"contacts": "all"}}
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_dict_contacts_list_match(self):
        scope = {"messages": {"contacts": ["pk1"]}}
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_dict_contacts_list_no_match(self):
        scope = {"messages": {"contacts": ["pk1"]}}
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk2"})

    def test_dict_channels_except_excludes_listed(self):
        scope = {"messages": {"channels": {"except": ["ch1"]}, "contacts": "all"}}
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})

    def test_dict_channels_except_includes_unlisted(self):
        scope = {"messages": {"channels": {"except": ["ch1"]}, "contacts": "all"}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch2"})

    def test_dict_contacts_except_excludes_listed(self):
        scope = {"messages": {"channels": "all", "contacts": {"except": ["pk1"]}}}
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_dict_contacts_except_includes_unlisted(self):
        scope = {"messages": {"channels": "all", "contacts": {"except": ["pk1"]}}}
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk2"})

    def test_dict_channels_except_empty_matches_all(self):
        scope = {"messages": {"channels": {"except": []}}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})


class TestScopeMatchesRaw:
    def test_all_matches(self):
        assert _scope_matches_raw({"raw_packets": "all"}, {})

    def test_none_does_not_match(self):
        assert not _scope_matches_raw({"raw_packets": "none"}, {})

    def test_missing_key_does_not_match(self):
        assert not _scope_matches_raw({}, {})


# ---------------------------------------------------------------------------
# FanoutManager dispatch tests
# ---------------------------------------------------------------------------


class StubModule(FanoutModule):
    """Minimal FanoutModule for testing dispatch."""

    def __init__(self):
        super().__init__("stub", {})
        self.message_calls: list[dict] = []
        self.raw_calls: list[dict] = []
        self.contact_calls: list[dict] = []
        self.telemetry_calls: list[dict] = []
        self.health_calls: list[dict] = []
        self._status = "connected"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def on_message(self, data: dict) -> None:
        self.message_calls.append(data)

    async def on_raw(self, data: dict) -> None:
        self.raw_calls.append(data)

    async def on_contact(self, data: dict) -> None:
        self.contact_calls.append(data)

    async def on_telemetry(self, data: dict) -> None:
        self.telemetry_calls.append(data)

    async def on_health(self, data: dict) -> None:
        self.health_calls.append(data)

    @property
    def status(self) -> str:
        return self._status


class TestFanoutManagerDispatch:
    @pytest.mark.asyncio
    async def test_broadcast_message_dispatches_to_matching_module(self):
        manager = FanoutManager()
        mod = StubModule()
        scope = {"messages": "all", "raw_packets": "none"}
        manager._modules["test-id"] = (mod, scope)

        await manager.broadcast_message({"type": "PRIV", "conversation_key": "pk1"})

        assert len(mod.message_calls) == 1
        assert mod.message_calls[0]["conversation_key"] == "pk1"

    @pytest.mark.asyncio
    async def test_broadcast_message_skips_non_matching_module(self):
        manager = FanoutManager()
        mod = StubModule()
        scope = {"messages": "none", "raw_packets": "all"}
        manager._modules["test-id"] = (mod, scope)

        await manager.broadcast_message({"type": "PRIV", "conversation_key": "pk1"})

        assert len(mod.message_calls) == 0

    @pytest.mark.asyncio
    async def test_broadcast_raw_dispatches_to_matching_module(self):
        manager = FanoutManager()
        mod = StubModule()
        scope = {"messages": "none", "raw_packets": "all"}
        manager._modules["test-id"] = (mod, scope)

        await manager.broadcast_raw({"data": "aabbccdd"})

        assert len(mod.raw_calls) == 1

    @pytest.mark.asyncio
    async def test_broadcast_raw_skips_non_matching(self):
        manager = FanoutManager()
        mod = StubModule()
        scope = {"messages": "all", "raw_packets": "none"}
        manager._modules["test-id"] = (mod, scope)

        await manager.broadcast_raw({"data": "aabbccdd"})

        assert len(mod.raw_calls) == 0

    @pytest.mark.asyncio
    async def test_stop_all_stops_all_modules(self):
        manager = FanoutManager()
        mod1 = StubModule()
        mod1.stop = AsyncMock()
        mod2 = StubModule()
        mod2.stop = AsyncMock()
        manager._modules["id1"] = (mod1, {})
        manager._modules["id2"] = (mod2, {})

        await manager.stop_all()

        mod1.stop.assert_called_once()
        mod2.stop.assert_called_once()
        assert len(manager._modules) == 0

    @pytest.mark.asyncio
    async def test_module_error_does_not_halt_broadcast(self):
        manager = FanoutManager()
        bad_mod = StubModule()

        async def fail(data):
            raise RuntimeError("boom")

        bad_mod.on_message = fail
        good_mod = StubModule()

        manager._modules["bad"] = (bad_mod, {"messages": "all"})
        manager._modules["good"] = (good_mod, {"messages": "all"})

        await manager.broadcast_message({"type": "PRIV", "conversation_key": "pk1"})

        # Good module should still receive the message despite the bad one failing
        assert len(good_mod.message_calls) == 1

    @pytest.mark.asyncio
    async def test_broadcast_message_dispatches_matching_modules_concurrently(self):
        manager = FanoutManager()

        class BlockingModule(StubModule):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def on_message(self, data: dict) -> None:
                self.started.set()
                await self.release.wait()
                self.message_calls.append(data)

        slow_mod = BlockingModule()
        fast_mod = StubModule()

        manager._modules["slow"] = (slow_mod, {"messages": "all"})
        manager._modules["fast"] = (fast_mod, {"messages": "all"})

        broadcast_task = asyncio.create_task(
            manager.broadcast_message({"type": "PRIV", "conversation_key": "pk1"})
        )

        await slow_mod.started.wait()
        await asyncio.sleep(0)

        assert len(fast_mod.message_calls) == 1
        assert not broadcast_task.done()

        slow_mod.release.set()
        await broadcast_task

    @pytest.mark.asyncio
    async def test_timed_out_module_is_restarted(self):
        manager = FanoutManager()
        mod = StubModule()
        mod.start = AsyncMock()
        mod.stop = AsyncMock()

        async def slow_message(data: dict) -> None:
            await asyncio.sleep(_DISPATCH_TIMEOUT_SECONDS * 2)

        mod.on_message = slow_message
        manager._modules["slow"] = (mod, {"messages": "all"})

        with patch("app.fanout.manager._DISPATCH_TIMEOUT_SECONDS", 0.01):
            await manager.broadcast_message({"type": "PRIV", "conversation_key": "pk1"})

        mod.stop.assert_called_once()
        mod.start.assert_called_once()

    def test_get_statuses(self):
        manager = FanoutManager()
        mod = StubModule()
        mod._status = "connected"
        manager._modules["test-id"] = (mod, {})

        with patch(
            "app.repository.fanout._configs_cache",
            {"test-id": {"name": "Test", "type": "mqtt_private"}},
        ):
            statuses = manager.get_statuses()

        assert "test-id" in statuses
        assert statuses["test-id"]["status"] == "connected"
        assert statuses["test-id"]["name"] == "Test"
        assert statuses["test-id"]["type"] == "mqtt_private"

    def test_get_statuses_includes_last_error(self):
        manager = FanoutManager()
        mod = StubModule()
        mod._status = "error"
        mod._last_error = "HTTP 500"
        manager._modules["test-id"] = (mod, {})

        with patch(
            "app.repository.fanout._configs_cache",
            {"test-id": {"name": "Test", "type": "webhook", "enabled": True}},
        ):
            statuses = manager.get_statuses()

        assert statuses["test-id"]["status"] == "error"
        assert statuses["test-id"]["last_error"] == "HTTP 500"

    def test_get_statuses_includes_start_failure_error(self):
        manager = FanoutManager()
        manager._module_errors["test-id"] = "ConnectionError: broker down"

        with patch(
            "app.repository.fanout._configs_cache",
            {"test-id": {"name": "Test", "type": "mqtt_private", "enabled": True}},
        ):
            statuses = manager.get_statuses()

        assert statuses["test-id"]["status"] == "error"
        assert statuses["test-id"]["last_error"] == "ConnectionError: broker down"


# ---------------------------------------------------------------------------
# New event dispatch (contact, telemetry, health)
# ---------------------------------------------------------------------------


class TestFanoutManagerNewEventDispatch:
    @pytest.mark.asyncio
    async def test_broadcast_contact_dispatches_to_all_modules(self):
        manager = FanoutManager()
        mod = StubModule()
        manager._modules["test-id"] = (mod, {})

        await manager.broadcast_contact({"public_key": "aabb", "name": "Alice"})

        assert len(mod.contact_calls) == 1
        assert mod.contact_calls[0]["public_key"] == "aabb"

    @pytest.mark.asyncio
    async def test_broadcast_contact_ignores_scope(self):
        """Contact dispatch is unconditional — scope doesn't affect it."""
        manager = FanoutManager()
        mod = StubModule()
        manager._modules["test-id"] = (mod, {"messages": "none", "raw_packets": "none"})

        await manager.broadcast_contact({"public_key": "aabb"})

        assert len(mod.contact_calls) == 1

    @pytest.mark.asyncio
    async def test_broadcast_telemetry_dispatches_to_all_modules(self):
        manager = FanoutManager()
        mod = StubModule()
        manager._modules["test-id"] = (mod, {})

        await manager.broadcast_telemetry(
            {"public_key": "ccdd", "battery_volts": 4.1, "timestamp": 1000}
        )

        assert len(mod.telemetry_calls) == 1
        assert mod.telemetry_calls[0]["battery_volts"] == 4.1

    @pytest.mark.asyncio
    async def test_broadcast_health_fanout_dispatches_to_all_modules(self):
        manager = FanoutManager()
        mod = StubModule()
        manager._modules["test-id"] = (mod, {})

        await manager.broadcast_health_fanout({"connected": True, "noise_floor_dbm": -112})

        assert len(mod.health_calls) == 1
        assert mod.health_calls[0]["connected"] is True

    @pytest.mark.asyncio
    async def test_new_events_do_not_affect_message_or_raw(self):
        """Verify new dispatch paths are independent of message/raw."""
        manager = FanoutManager()
        mod = StubModule()
        manager._modules["test-id"] = (mod, {"messages": "all", "raw_packets": "all"})

        await manager.broadcast_contact({"public_key": "aabb"})
        await manager.broadcast_telemetry({"public_key": "ccdd", "battery_volts": 3.8})
        await manager.broadcast_health_fanout({"connected": False})

        assert len(mod.message_calls) == 0
        assert len(mod.raw_calls) == 0
        assert len(mod.contact_calls) == 1
        assert len(mod.telemetry_calls) == 1
        assert len(mod.health_calls) == 1

    @pytest.mark.asyncio
    async def test_base_module_no_ops_do_not_raise(self):
        """Default FanoutModule no-ops accept data without error."""
        manager = FanoutManager()

        class MinimalModule(FanoutModule):
            @property
            def status(self) -> str:
                return "connected"

        mod = MinimalModule("test", {})
        manager._modules["test-id"] = (mod, {})

        # Should not raise — base class no-ops silently accept
        await manager.broadcast_contact({"public_key": "aabb"})
        await manager.broadcast_telemetry({"public_key": "ccdd"})
        await manager.broadcast_health_fanout({"connected": True})

    @pytest.mark.asyncio
    async def test_error_in_one_module_does_not_block_others(self):
        manager = FanoutManager()

        bad_mod = StubModule()

        async def fail(data):
            raise RuntimeError("boom")

        bad_mod.on_contact = fail

        good_mod = StubModule()
        manager._modules["bad"] = (bad_mod, {})
        manager._modules["good"] = (good_mod, {})

        await manager.broadcast_contact({"public_key": "aabb"})

        assert len(good_mod.contact_calls) == 1


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def fanout_db():
    """Create an in-memory database with fanout_configs table."""
    import app.repository.fanout as fanout_mod

    db = Database(":memory:")
    await db.connect()

    await db.conn.execute("""
        CREATE TABLE IF NOT EXISTS fanout_configs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            config TEXT NOT NULL DEFAULT '{}',
            scope TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0
        )
    """)
    await db.conn.commit()

    original_db = fanout_mod.db
    fanout_mod.db = db

    try:
        yield db
    finally:
        fanout_mod.db = original_db
        await db.disconnect()


class TestFanoutConfigRepository:
    @pytest.mark.asyncio
    async def test_create_and_get(self, fanout_db):
        from app.repository.fanout import FanoutConfigRepository

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Test MQTT",
            config={"broker_host": "localhost", "broker_port": 1883},
            scope={"messages": "all", "raw_packets": "all"},
            enabled=True,
        )

        assert cfg["type"] == "mqtt_private"
        assert cfg["name"] == "Test MQTT"
        assert cfg["enabled"] is True
        assert cfg["config"]["broker_host"] == "localhost"

        fetched = await FanoutConfigRepository.get(cfg["id"])
        assert fetched is not None
        assert fetched["id"] == cfg["id"]

    @pytest.mark.asyncio
    async def test_get_all(self, fanout_db):
        from app.repository.fanout import FanoutConfigRepository

        await FanoutConfigRepository.create(
            config_type="mqtt_private", name="A", config={}, scope={}, enabled=True
        )
        await FanoutConfigRepository.create(
            config_type="mqtt_community", name="B", config={}, scope={}, enabled=False
        )

        all_configs = await FanoutConfigRepository.get_all()
        assert len(all_configs) == 2

    @pytest.mark.asyncio
    async def test_update(self, fanout_db):
        from app.repository.fanout import FanoutConfigRepository

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Original",
            config={"broker_host": "old"},
            scope={},
            enabled=True,
        )

        updated = await FanoutConfigRepository.update(
            cfg["id"],
            name="Renamed",
            config={"broker_host": "new"},
            enabled=False,
        )

        assert updated is not None
        assert updated["name"] == "Renamed"
        assert updated["config"]["broker_host"] == "new"
        assert updated["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete(self, fanout_db):
        from app.repository.fanout import FanoutConfigRepository

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_private", name="Doomed", config={}, scope={}, enabled=True
        )

        await FanoutConfigRepository.delete(cfg["id"])

        assert await FanoutConfigRepository.get(cfg["id"]) is None

    @pytest.mark.asyncio
    async def test_get_enabled(self, fanout_db):
        from app.repository.fanout import FanoutConfigRepository

        await FanoutConfigRepository.create(
            config_type="mqtt_private", name="On", config={}, scope={}, enabled=True
        )
        await FanoutConfigRepository.create(
            config_type="mqtt_community", name="Off", config={}, scope={}, enabled=False
        )

        enabled = await FanoutConfigRepository.get_enabled()
        assert len(enabled) == 1
        assert enabled[0]["name"] == "On"


# ---------------------------------------------------------------------------
# broadcast_event realtime=False test
# ---------------------------------------------------------------------------


class TestBroadcastEventRealtime:
    @pytest.mark.asyncio
    async def test_realtime_false_does_not_dispatch_fanout(self):
        """broadcast_event with realtime=False should NOT trigger fanout dispatch."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()

            broadcast_event("message", {"type": "PRIV"}, realtime=False)

            # Allow tasks to run
            import asyncio

            await asyncio.sleep(0)

            # WebSocket broadcast should still fire
            mock_ws.broadcast.assert_called_once()
            # But fanout should NOT be called
            mock_fm.broadcast_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_realtime_true_dispatches_fanout(self):
        """broadcast_event with realtime=True should trigger fanout dispatch."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_fm.broadcast_message = AsyncMock()

            broadcast_event("message", {"type": "PRIV"}, realtime=True)

            import asyncio

            await asyncio.sleep(0)

            mock_ws.broadcast.assert_called_once()
            mock_fm.broadcast_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_contact_event_dispatches_to_fanout(self):
        """broadcast_event for 'contact' should trigger fanout contact dispatch."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()
            mock_fm.broadcast_contact = AsyncMock()

            broadcast_event("contact", {"public_key": "aabb"}, realtime=True)

            import asyncio

            await asyncio.sleep(0)

            mock_ws.broadcast.assert_called_once()
            mock_fm.broadcast_contact.assert_called_once()

    @pytest.mark.asyncio
    async def test_contact_event_skipped_when_not_realtime(self):
        """broadcast_event('contact', ..., realtime=False) should skip fanout."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_ws.broadcast = AsyncMock()

            broadcast_event("contact", {"public_key": "aabb"}, realtime=False)

            import asyncio

            await asyncio.sleep(0)

            mock_ws.broadcast.assert_called_once()
            mock_fm.broadcast_contact.assert_not_called()


# ---------------------------------------------------------------------------
# Webhook module unit tests
# ---------------------------------------------------------------------------


class TestWebhookModule:
    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_url(self):
        from app.fanout.webhook import WebhookModule

        mod = WebhookModule("test", {"url": ""})
        await mod.start()
        assert mod.status == "disconnected"
        await mod.stop()

    @pytest.mark.asyncio
    async def test_status_connected_with_url(self):
        from app.fanout.webhook import WebhookModule

        mod = WebhookModule("test", {"url": "http://localhost:9999/hook"})
        await mod.start()
        assert mod.status == "connected"
        await mod.stop()

    @pytest.mark.asyncio
    async def test_does_not_skip_outgoing_messages(self):
        """Webhook should forward outgoing messages (unlike Apprise)."""
        from app.fanout.webhook import WebhookModule

        mod = WebhookModule("test", {"url": "http://localhost:9999/hook"})
        await mod.start()
        # Mock the client to capture the request
        sent_data: list[dict] = []

        async def capture_send(data: dict, *, event_type: str) -> None:
            sent_data.append(data)

        mod._send = capture_send
        await mod.on_message({"type": "PRIV", "text": "outgoing", "outgoing": True})
        assert len(sent_data) == 1
        assert sent_data[0]["outgoing"] is True
        await mod.stop()

    @pytest.mark.asyncio
    async def test_dispatch_with_matching_scope(self):
        """WebhookModule dispatches through FanoutManager scope matching."""
        manager = FanoutManager()
        mod = StubModule()
        scope = {"messages": {"channels": ["ch1"], "contacts": "none"}, "raw_packets": "none"}
        manager._modules["test-webhook"] = (mod, scope)

        await manager.broadcast_message({"type": "CHAN", "conversation_key": "ch1", "text": "yes"})
        await manager.broadcast_message({"type": "CHAN", "conversation_key": "ch2", "text": "no"})
        await manager.broadcast_message(
            {"type": "PRIV", "conversation_key": "pk1", "text": "dm no"}
        )

        assert len(mod.message_calls) == 1
        assert mod.message_calls[0]["text"] == "yes"


# ---------------------------------------------------------------------------
# Webhook router validation tests
# ---------------------------------------------------------------------------


class TestWebhookValidation:
    def test_validate_webhook_config_requires_url(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_webhook_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_webhook_config({"url": ""})
        assert exc_info.value.status_code == 400
        assert "url is required" in exc_info.value.detail

    def test_validate_webhook_config_requires_http_scheme(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_webhook_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_webhook_config({"url": "ftp://example.com"})
        assert exc_info.value.status_code == 400

    def test_validate_webhook_config_rejects_bad_method(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_webhook_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_webhook_config({"url": "https://example.com/hook", "method": "DELETE"})
        assert exc_info.value.status_code == 400
        assert "method" in exc_info.value.detail

    def test_validate_webhook_config_accepts_valid(self):
        from app.routers.fanout import _validate_webhook_config

        # Should not raise
        _validate_webhook_config(
            {"url": "https://example.com/hook", "method": "POST", "headers": {}}
        )

    def test_enforce_scope_webhook_strips_raw_packets(self):
        from app.routers.fanout import _enforce_scope

        scope = _enforce_scope("webhook", {"messages": "all", "raw_packets": "all"})
        assert scope["raw_packets"] == "none"
        assert scope["messages"] == "all"

    def test_enforce_scope_webhook_preserves_selective(self):
        from app.routers.fanout import _enforce_scope

        scope = _enforce_scope(
            "webhook",
            {"messages": {"channels": ["ch1"], "contacts": "none"}, "raw_packets": "all"},
        )
        assert scope["raw_packets"] == "none"
        assert scope["messages"] == {"channels": ["ch1"], "contacts": "none"}


# ---------------------------------------------------------------------------
# SQS module unit tests
# ---------------------------------------------------------------------------


class TestSqsModule:
    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_queue_url(self):
        from app.fanout.sqs import SqsModule

        with patch("app.fanout.sqs.boto3.client", return_value=MagicMock()):
            mod = SqsModule("test", {"queue_url": ""})
            await mod.start()
            assert mod.status == "disconnected"
            await mod.stop()

    @pytest.mark.asyncio
    async def test_status_connected_with_queue_url(self):
        from app.fanout.sqs import SqsModule

        with patch("app.fanout.sqs.boto3.client", return_value=MagicMock()) as mock_client:
            mod = SqsModule(
                "test",
                {"queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events"},
            )
            await mod.start()
            assert mod.status == "connected"
            mock_client.assert_called_once_with("sqs", region_name="us-east-1")
            await mod.stop()

    @pytest.mark.asyncio
    async def test_explicit_region_overrides_queue_url_inference(self):
        from app.fanout.sqs import SqsModule

        with patch("app.fanout.sqs.boto3.client", return_value=MagicMock()) as mock_client:
            mod = SqsModule(
                "test",
                {
                    "queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events",
                    "region_name": "us-west-2",
                },
            )
            await mod.start()
            mock_client.assert_called_once_with("sqs", region_name="us-west-2")
            await mod.stop()

    @pytest.mark.asyncio
    async def test_custom_queue_url_does_not_infer_region(self):
        from app.fanout.sqs import SqsModule

        with patch("app.fanout.sqs.boto3.client", return_value=MagicMock()) as mock_client:
            mod = SqsModule(
                "test",
                {"queue_url": "https://localhost:4566/000000000000/mesh-events"},
            )
            await mod.start()
            mock_client.assert_called_once_with("sqs")
            await mod.stop()

    @pytest.mark.asyncio
    async def test_sends_message_payload(self):
        from app.fanout.sqs import SqsModule

        mock_client = MagicMock()
        with patch("app.fanout.sqs.boto3.client", return_value=mock_client):
            mod = SqsModule(
                "test",
                {"queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events"},
            )
            await mod.start()
            await mod.on_message(
                {"id": 42, "type": "PRIV", "conversation_key": "pk1", "text": "hi"}
            )

        mock_client.send_message.assert_called_once()
        kwargs = mock_client.send_message.call_args.kwargs
        assert kwargs["QueueUrl"].endswith("/mesh-events")
        assert kwargs["MessageAttributes"]["event_type"]["StringValue"] == "message"
        assert '"event_type":"message"' in kwargs["MessageBody"]
        assert '"text":"hi"' in kwargs["MessageBody"]

    @pytest.mark.asyncio
    async def test_fifo_queue_adds_group_and_dedup_ids(self):
        from app.fanout.sqs import SqsModule

        mock_client = MagicMock()
        with patch("app.fanout.sqs.boto3.client", return_value=mock_client):
            mod = SqsModule(
                "test",
                {"queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events.fifo"},
            )
            await mod.start()
            await mod.on_raw({"observation_id": "obs-123", "id": 7, "raw": "abcd"})

        kwargs = mock_client.send_message.call_args.kwargs
        assert kwargs["MessageGroupId"] == "raw-packets"
        assert kwargs["MessageDeduplicationId"] == "raw-obs-123"


# ---------------------------------------------------------------------------
# SQS router validation tests
# ---------------------------------------------------------------------------


class TestSqsValidation:
    def test_validate_sqs_config_requires_queue_url(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_sqs_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_sqs_config({"queue_url": ""})
        assert exc_info.value.status_code == 400
        assert "queue_url is required" in exc_info.value.detail

    def test_validate_sqs_config_requires_static_keypair_together(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_sqs_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_sqs_config(
                {
                    "queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events",
                    "access_key_id": "AKIA...",
                }
            )
        assert exc_info.value.status_code == 400
        assert "must be set together" in exc_info.value.detail

    def test_validate_sqs_config_accepts_minimal_valid_config(self):
        from app.routers.fanout import _validate_sqs_config

        _validate_sqs_config(
            {"queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events"}
        )


class TestMapUploadValidation:
    def test_rejects_bad_api_url_scheme(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_map_upload_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_map_upload_config({"api_url": "ftp://example.com"})
        assert exc_info.value.status_code == 400
        assert "api_url" in exc_info.value.detail

    def test_accepts_empty_api_url(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"api_url": ""}
        _validate_map_upload_config(config)
        assert config["api_url"] == ""

    def test_accepts_valid_api_url(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"api_url": "https://custom.example.com/upload"}
        _validate_map_upload_config(config)
        assert config["api_url"] == "https://custom.example.com/upload"

    def test_normalizes_dry_run_to_bool(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"dry_run": 1}
        _validate_map_upload_config(config)
        assert config["dry_run"] is True

    def test_normalizes_geofence_enabled_to_bool(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"geofence_enabled": 1}
        _validate_map_upload_config(config)
        assert config["geofence_enabled"] is True

    def test_normalizes_geofence_radius_to_float(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"geofence_radius_km": 100}
        _validate_map_upload_config(config)
        assert config["geofence_radius_km"] == 100.0
        assert isinstance(config["geofence_radius_km"], float)

    def test_rejects_negative_geofence_radius(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_map_upload_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_map_upload_config({"geofence_radius_km": -1})
        assert exc_info.value.status_code == 400
        assert "geofence_radius_km" in exc_info.value.detail

    def test_rejects_non_numeric_geofence_radius(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_map_upload_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_map_upload_config({"geofence_radius_km": "bad"})
        assert exc_info.value.status_code == 400
        assert "geofence_radius_km" in exc_info.value.detail

    def test_accepts_zero_geofence_radius(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {"geofence_radius_km": 0}
        _validate_map_upload_config(config)
        assert config["geofence_radius_km"] == 0.0

    def test_defaults_applied_when_keys_absent(self):
        from app.routers.fanout import _validate_map_upload_config

        config = {}
        _validate_map_upload_config(config)
        assert config["api_url"] == ""
        assert config["dry_run"] is True
        assert config["geofence_enabled"] is False
        assert config["geofence_radius_km"] == 0.0

    def test_enforce_scope_map_upload_forces_raw_only(self):
        """map_upload scope is always fixed regardless of what the caller passes."""
        from app.routers.fanout import _enforce_scope

        scope = _enforce_scope("map_upload", {"messages": "all", "raw_packets": "none"})
        assert scope == {"messages": "none", "raw_packets": "all"}

    def test_enforce_scope_sqs_preserves_raw_packets_setting(self):
        from app.routers.fanout import _enforce_scope

        scope = _enforce_scope("sqs", {"messages": "all", "raw_packets": "all"})
        assert scope["messages"] == "all"
        assert scope["raw_packets"] == "all"


# ---------------------------------------------------------------------------
# Apprise module unit tests
# ---------------------------------------------------------------------------


class TestAppriseModule:
    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_urls(self):
        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": ""})
        assert mod.status == "disconnected"

    @pytest.mark.asyncio
    async def test_status_connected_with_urls(self):
        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost"})
        assert mod.status == "connected"

    @pytest.mark.asyncio
    async def test_skips_outgoing_messages(self):
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost"})
        with _patch("app.fanout.apprise_mod._send_sync") as mock_send:
            await mod.on_message({"type": "PRIV", "text": "hi", "outgoing": True})
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_for_incoming_messages(self):
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost"})
        with _patch("app.fanout.apprise_mod._send_sync", return_value=True) as mock_send:
            await mod.on_message(
                {"type": "PRIV", "text": "hello", "outgoing": False, "sender_name": "Alice"}
            )
            mock_send.assert_called_once()
            body = mock_send.call_args[0][1]
            assert "Alice" in body
            assert "hello" in body


class TestAppriseFormatBody:
    def test_dm_format(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            body_format_dm="**DM:** {sender_name}: {text}",
        )
        assert body == "**DM:** Alice: hi"

    def test_channel_format(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "CHAN", "text": "hi", "sender_name": "Bob", "channel_name": "#general"},
            body_format_channel="**{channel_name}:** {sender_name}: {text}",
        )
        assert body == "**#general:** Bob: hi"

    def test_channel_format_strips_stored_sender_prefix(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "CHAN",
                "text": "Bob: hi",
                "sender_name": "Bob",
                "channel_name": "#general",
            },
            body_format_channel="**{channel_name}:** {sender_name}: {text}",
        )
        assert body == "**#general:** Bob: hi"

    def test_dm_with_path(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "2027"}],
            },
            body_format_dm="**DM:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "**via:**" in body
        assert "`20`" in body
        assert "`27`" in body

    def test_dm_no_path_shows_direct(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            body_format_dm="**DM:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "`direct`" in body

    def test_dm_with_2byte_hop_path(self):
        """Multi-byte (2-byte) hops are correctly split using path_len metadata."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "aabbccdd", "path_len": 2}],
            },
            body_format_dm="**DM:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "**via:**" in body
        assert "`aabb`" in body
        assert "`ccdd`" in body

    def test_dm_with_3byte_hop_path(self):
        """Multi-byte (3-byte) hops are correctly split using path_len metadata."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "aabbccddeeff", "path_len": 2}],
            },
            body_format_dm="**DM:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "**via:**" in body
        assert "`aabbcc`" in body
        assert "`ddeeff`" in body

    def test_channel_with_multibyte_path(self):
        """Channel message with 2-byte hop path_len metadata."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "CHAN",
                "text": "hi",
                "sender_name": "Bob",
                "channel_name": "#general",
                "paths": [{"path": "aabbccdd", "path_len": 2}],
            },
            body_format_channel="**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "**#general:**" in body
        assert "`aabb`" in body
        assert "`ccdd`" in body

    def test_legacy_path_without_path_len(self):
        """Legacy path (no path_len) falls back to 2-char chunks."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "aabb"}],
            },
            body_format_dm="**DM:** {sender_name}: {text} **via:** [{hops_backticked}]",
        )
        assert "**via:**" in body
        assert "`aa`" in body
        assert "`bb`" in body

    def test_default_format_strings(self):
        """Default format strings produce expected output."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "2a3b"}],
            },
        )
        assert body == "**DM:** Alice: hi **via:** [`2a`, `3b`]"

    def test_custom_format_with_rssi(self):
        """Custom format string can include rssi/snr."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": "2a", "rssi": -95, "snr": 6.5}],
            },
            body_format_dm="From {sender_name}: {text} (rssi: {rssi}, snr: {snr})",
        )
        assert body == "From Alice: hi (rssi: -95, snr: 6.5)"

    def test_unknown_placeholder_left_as_is(self):
        """Unknown {placeholders} pass through unchanged."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            body_format_dm="{sender_name}: {text} {unknown_var}",
        )
        assert body == "Alice: hi {unknown_var}"

    def test_none_fields_render_empty(self):
        """None optional fields render as empty string, not 'None'."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            body_format_dm="{sender_name}: {text} rssi={rssi}",
        )
        assert body == "Alice: hi rssi="
        assert "None" not in body

    def test_hops_direct_when_no_paths(self):
        """hops is 'direct' when no path data exists."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "CHAN", "text": "hi", "sender_name": "Bob", "channel_name": "#gen"},
            body_format_channel="{channel_name} {hops}",
        )
        assert body == "#gen direct"

    def test_hops_direct_when_empty_path(self):
        """hops is 'direct' when path string is empty."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {
                "type": "PRIV",
                "text": "hi",
                "sender_name": "Alice",
                "paths": [{"path": ""}],
            },
            body_format_dm="{hops}",
        )
        assert body == "direct"

    def test_no_re_expansion_of_substituted_values(self):
        """Placeholders in message text must not be expanded by later passes."""
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hello {sender_name}", "sender_name": "Alice"},
            body_format_dm="{sender_name}: {text}",
        )
        assert body == "Alice: hello {sender_name}"

    @pytest.mark.asyncio
    async def test_empty_format_string_uses_default(self):
        """Empty format strings in config should produce default output, not blank."""
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule(
            "test",
            {"urls": "json://localhost", "body_format_dm": "", "body_format_channel": "  "},
        )
        with _patch("app.fanout.apprise_mod._send_sync", return_value=True) as mock_send:
            await mod.on_message(
                {"type": "PRIV", "text": "hi", "outgoing": False, "sender_name": "Alice"}
            )
            mock_send.assert_called_once()
            body = mock_send.call_args[0][1]
            assert "Alice" in body
            assert "hi" in body
            assert body != ""


class TestAppriseNormalizeDiscordUrl:
    def test_discord_scheme(self):
        from app.fanout.apprise_mod import _normalize_discord_url

        assert _normalize_discord_url("discord://123/abc") == "discord://123/abc?avatar=no"

    def test_discord_https(self):
        from app.fanout.apprise_mod import _normalize_discord_url

        result = _normalize_discord_url("https://discord.com/api/webhooks/123/abc")
        assert "avatar=no" in result


class TestFanoutMessageText:
    def test_channel_text_strips_matching_sender_prefix(self):
        from app.fanout.base import get_fanout_message_text

        text = get_fanout_message_text(
            {
                "type": "CHAN",
                "text": "Alice: hello world",
                "sender_name": "Alice",
            }
        )

        assert text == "hello world"

    def test_channel_text_keeps_nonmatching_prefix(self):
        from app.fanout.base import get_fanout_message_text

        text = get_fanout_message_text(
            {
                "type": "CHAN",
                "text": "Alice: hello world",
                "sender_name": "Bob",
            }
        )

        assert text == "Alice: hello world"

    def test_non_discord_unchanged(self):
        from app.fanout.apprise_mod import _normalize_discord_url

        url = "slack://token_a/token_b/token_c"
        assert _normalize_discord_url(url) == url


class TestAppriseValidation:
    def test_validate_apprise_config_requires_urls(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_apprise_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_apprise_config({"urls": ""})
        assert exc_info.value.status_code == 400

    def test_validate_apprise_config_accepts_valid(self):
        from app.routers.fanout import _validate_apprise_config

        _validate_apprise_config({"urls": "discord://123/abc"})

    def test_validate_apprise_config_accepts_format_strings(self):
        from app.routers.fanout import _validate_apprise_config

        _validate_apprise_config(
            {
                "urls": "discord://123/abc",
                "body_format_dm": "DM from {sender_name}: {text}",
                "body_format_channel": "{channel_name}: {text}",
            }
        )

    def test_validate_apprise_config_rejects_non_string_format(self):
        from fastapi import HTTPException

        from app.routers.fanout import _validate_apprise_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_apprise_config({"urls": "discord://123/abc", "body_format_dm": 123})
        assert exc_info.value.status_code == 400

    def test_enforce_scope_apprise_strips_raw_packets(self):
        from app.routers.fanout import _enforce_scope

        scope = _enforce_scope("apprise", {"messages": "all", "raw_packets": "all"})
        assert scope["raw_packets"] == "none"
        assert scope["messages"] == "all"

    def test_validate_apprise_config_accepts_markdown_format_bool(self):
        from app.routers.fanout import _validate_apprise_config

        _validate_apprise_config({"urls": "discord://123/abc", "markdown_format": False})

    def test_validate_apprise_config_normalizes_markdown_format(self):
        from app.routers.fanout import _validate_apprise_config

        config: dict = {"urls": "discord://123/abc", "markdown_format": 0}
        _validate_apprise_config(config)
        assert config["markdown_format"] is False

    def test_validate_apprise_config_works_without_markdown_format(self):
        from app.routers.fanout import _validate_apprise_config

        _validate_apprise_config({"urls": "discord://123/abc"})


class TestAppriseMarkdownFormat:
    def test_format_body_markdown_true_uses_markdown_fallback(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            markdown=True,
        )
        assert "**DM:**" in body

    def test_format_body_markdown_false_uses_plain_fallback(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "PRIV", "text": "hi", "sender_name": "Alice"},
            markdown=False,
        )
        assert "**" not in body
        assert "DM:" in body
        assert "Alice" in body

    def test_format_body_markdown_false_channel(self):
        from app.fanout.apprise_mod import _format_body

        body = _format_body(
            {"type": "CHAN", "text": "hi", "sender_name": "Bob", "channel_name": "#gen"},
            markdown=False,
        )
        assert "**" not in body
        assert "#gen:" in body

    def test_send_sync_passes_markdown_body_format(self):
        from unittest.mock import MagicMock, patch

        with patch("app.fanout.apprise_mod.apprise_lib", create=True) as mock_lib:
            mock_notifier = MagicMock()
            mock_notifier.notify.return_value = True
            mock_lib.Apprise.return_value = mock_notifier

            with patch.dict("sys.modules", {"apprise": mock_lib}):
                from app.fanout.apprise_mod import _send_sync

                _send_sync("json://localhost", "test", preserve_identity=False, markdown=True)
                call_kwargs = mock_notifier.notify.call_args
                assert call_kwargs.kwargs.get("body_format") or call_kwargs[1].get("body_format")

    def test_send_sync_passes_text_body_format_when_markdown_false(self):
        from unittest.mock import MagicMock, patch

        with patch("app.fanout.apprise_mod.apprise_lib", create=True) as mock_lib:
            mock_notifier = MagicMock()
            mock_notifier.notify.return_value = True
            mock_lib.Apprise.return_value = mock_notifier

            with patch.dict("sys.modules", {"apprise": mock_lib}):
                from app.fanout.apprise_mod import _send_sync

                _send_sync("json://localhost", "test", preserve_identity=False, markdown=False)
                call_kwargs = mock_notifier.notify.call_args
                assert call_kwargs.kwargs.get("body_format") or call_kwargs[1].get("body_format")

    @pytest.mark.asyncio
    async def test_on_message_reads_markdown_format_config(self):
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost", "markdown_format": False})
        with _patch("app.fanout.apprise_mod._send_sync", return_value=True) as mock_send:
            await mod.on_message(
                {"type": "PRIV", "text": "hello", "outgoing": False, "sender_name": "S_Borkin"}
            )
            mock_send.assert_called_once()
            assert mock_send.call_args.kwargs.get("markdown") is False

    @pytest.mark.asyncio
    async def test_on_message_defaults_markdown_true(self):
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost"})
        with _patch("app.fanout.apprise_mod._send_sync", return_value=True) as mock_send:
            await mod.on_message(
                {"type": "PRIV", "text": "hello", "outgoing": False, "sender_name": "Alice"}
            )
            mock_send.assert_called_once()
            assert mock_send.call_args.kwargs.get("markdown") is True

    @pytest.mark.asyncio
    async def test_on_message_markdown_false_uses_plain_default_format(self):
        from unittest.mock import patch as _patch

        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("test", {"urls": "json://localhost", "markdown_format": False})
        with _patch("app.fanout.apprise_mod._send_sync", return_value=True) as mock_send:
            await mod.on_message(
                {
                    "type": "CHAN",
                    "text": "hi",
                    "outgoing": False,
                    "sender_name": "Bob",
                    "channel_name": "#general",
                }
            )
            body = mock_send.call_args[0][1]
            assert "**" not in body
            assert "#general:" in body


# ---------------------------------------------------------------------------
# Comprehensive scope/filter selection logic tests
# ---------------------------------------------------------------------------


class TestMatchesFilter:
    """Test _matches_filter directly for all filter shapes."""

    def test_all_matches_any_key(self):
        from app.fanout.manager import _matches_filter

        assert _matches_filter("all", "anything")
        assert _matches_filter("all", "")
        assert _matches_filter("all", "special-chars-!@#")

    def test_none_matches_nothing(self):
        from app.fanout.manager import _matches_filter

        assert not _matches_filter("none", "anything")
        assert not _matches_filter("none", "")

    def test_list_matches_present_key(self):
        from app.fanout.manager import _matches_filter

        assert _matches_filter(["a", "b", "c"], "b")

    def test_list_no_match_absent_key(self):
        from app.fanout.manager import _matches_filter

        assert not _matches_filter(["a", "b"], "c")

    def test_list_empty_matches_nothing(self):
        from app.fanout.manager import _matches_filter

        assert not _matches_filter([], "anything")

    def test_except_excludes_listed(self):
        from app.fanout.manager import _matches_filter

        assert not _matches_filter({"except": ["blocked"]}, "blocked")

    def test_except_includes_unlisted(self):
        from app.fanout.manager import _matches_filter

        assert _matches_filter({"except": ["blocked"]}, "allowed")

    def test_except_empty_matches_everything(self):
        from app.fanout.manager import _matches_filter

        assert _matches_filter({"except": []}, "anything")
        assert _matches_filter({"except": []}, "")

    def test_except_multiple_excludes(self):
        from app.fanout.manager import _matches_filter

        filt = {"except": ["x", "y", "z"]}
        assert not _matches_filter(filt, "x")
        assert not _matches_filter(filt, "y")
        assert not _matches_filter(filt, "z")
        assert _matches_filter(filt, "a")

    def test_unrecognized_shape_returns_false(self):
        from app.fanout.manager import _matches_filter

        assert not _matches_filter(42, "key")
        assert not _matches_filter({"other": "thing"}, "key")
        assert not _matches_filter(True, "key")


class TestScopeMatchesMessageCombinations:
    """Test _scope_matches_message with complex combinations."""

    def test_channel_with_only_channels_listed(self):
        scope = {"messages": {"channels": ["ch1", "ch2"], "contacts": "all"}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch2"})
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch3"})

    def test_contact_with_only_contacts_listed(self):
        scope = {"messages": {"channels": "all", "contacts": ["pk1"]}}
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk2"})

    def test_mixed_channels_all_contacts_except(self):
        scope = {"messages": {"channels": "all", "contacts": {"except": ["pk-blocked"]}}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk-ok"})
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk-blocked"})

    def test_channels_except_contacts_only(self):
        scope = {
            "messages": {
                "channels": {"except": ["ch-muted"]},
                "contacts": ["pk-friend"],
            }
        }
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch-ok"})
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch-muted"})
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk-friend"})
        assert not _scope_matches_message(
            scope, {"type": "PRIV", "conversation_key": "pk-stranger"}
        )

    def test_both_channels_and_contacts_none(self):
        scope = {"messages": {"channels": "none", "contacts": "none"}}
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_both_channels_and_contacts_all(self):
        scope = {"messages": {"channels": "all", "contacts": "all"}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_missing_contacts_key_defaults_false(self):
        scope = {"messages": {"channels": "all"}}
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        # Missing contacts -> defaults to "none" -> no match for DMs
        assert not _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_missing_channels_key_defaults_false(self):
        scope = {"messages": {"contacts": "all"}}
        assert not _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})

    def test_unknown_message_type_no_match(self):
        scope = {"messages": {"channels": "all", "contacts": "all"}}
        assert not _scope_matches_message(scope, {"type": "UNKNOWN", "conversation_key": "x"})

    def test_both_except_empty_matches_everything(self):
        scope = {
            "messages": {
                "channels": {"except": []},
                "contacts": {"except": []},
            }
        }
        assert _scope_matches_message(scope, {"type": "CHAN", "conversation_key": "ch1"})
        assert _scope_matches_message(scope, {"type": "PRIV", "conversation_key": "pk1"})
