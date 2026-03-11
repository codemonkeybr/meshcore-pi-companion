"""Unit tests for ClientBackend (meshcore wrapper).

Phase 3: Verify ClientBackend correctly delegates to the underlying meshcore instance.
These tests use a mock meshcore; no real serial/TCP/BLE connection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.backends.client_backend import ClientBackend


class TestClientBackendDelegation:
    """ClientBackend delegates connection, commands, and events to meshcore."""

    @pytest.fixture
    def mc(self):
        """Mock MeshCore instance."""
        m = MagicMock()
        m.is_connected = True
        m.self_info = {"public_key": "ab" * 32, "adv_name": "TestRadio"}
        m.disconnect = AsyncMock()
        m.commands = MagicMock()
        m.commands.get_contacts = AsyncMock(return_value=[{"name": "Alice", "public_key": "aa" * 32}])
        m.commands.send_msg = AsyncMock(return_value=MagicMock(type=1, payload={"expected_ack": "abc"}))
        m.commands.get_msg = AsyncMock(return_value=MagicMock(type=0))  # NO_MORE_MSGS
        m.subscribe = MagicMock(return_value=MagicMock(unsubscribe=MagicMock()))
        m._contacts = {}
        return m

    @pytest.fixture
    def backend(self, mc):
        return ClientBackend(mc)

    def test_is_connected_delegates(self, backend, mc):
        assert backend.is_connected is True
        mc.is_connected = False
        assert backend.is_connected is False

    def test_self_info_delegates(self, backend, mc):
        assert backend.self_info["adv_name"] == "TestRadio"
        assert backend.self_info["public_key"] == "ab" * 32

    @pytest.mark.asyncio
    async def test_disconnect_calls_meshcore(self, backend, mc):
        await backend.disconnect()
        mc.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_contacts_delegates(self, backend, mc):
        result = await backend.get_contacts()
        mc.commands.get_contacts.assert_called_once()
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_send_msg_delegates_with_timestamp(self, backend, mc):
        dst = MagicMock()
        await backend.send_msg(dst, "Hello", timestamp=1700000000)
        mc.commands.send_msg.assert_called_once_with(dst=dst, msg="Hello", timestamp=1700000000)

    @pytest.mark.asyncio
    async def test_send_msg_delegates_without_timestamp(self, backend, mc):
        dst = MagicMock()
        await backend.send_msg(dst, "Hi")
        mc.commands.send_msg.assert_called_once_with(dst=dst, msg="Hi")

    def test_subscribe_returns_meshcore_subscription(self, backend, mc):
        handler = MagicMock()
        sub = backend.subscribe("RX_LOG_DATA", handler)
        mc.subscribe.assert_called_once_with("RX_LOG_DATA", handler)
        assert sub is mc.subscribe.return_value

    def test_get_contact_by_key_prefix_delegates(self, backend, mc):
        contact = MagicMock()
        contact.name = "Bob"
        contact.public_key = "bb" * 32
        mc.get_contact_by_key_prefix.return_value = contact
        result = backend.get_contact_by_key_prefix("bbbb")
        mc.get_contact_by_key_prefix.assert_called_once_with("bbbb")
        assert result.name == "Bob"

    def test_evict_contact_from_cache_delegates(self, backend, mc):
        mc._contacts = {"aabb": MagicMock()}
        backend.evict_contact_from_cache("aabb")
        assert "aabb" not in mc._contacts
