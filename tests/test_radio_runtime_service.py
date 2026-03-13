from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.radio_runtime import RadioRuntime


class _Manager:
    def __init__(
        self,
        *,
        meshcore=None,
        is_connected=False,
        is_reconnecting=False,
        is_setup_in_progress=False,
        is_setup_complete=False,
        connection_info=None,
        path_hash_mode=0,
        path_hash_mode_supported=False,
    ):
        self.meshcore = meshcore
        self.is_connected = is_connected
        self.is_reconnecting = is_reconnecting
        self.is_setup_in_progress = is_setup_in_progress
        self.is_setup_complete = is_setup_complete
        self.connection_info = connection_info
        self.path_hash_mode = path_hash_mode
        self.path_hash_mode_supported = path_hash_mode_supported
        self.calls: list[tuple[str, dict]] = []

    @asynccontextmanager
    async def radio_operation(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        yield self.meshcore


def test_uses_latest_manager_from_getter():
    first = _Manager(meshcore="mc1", is_connected=True, connection_info="first")
    second = _Manager(meshcore="mc2", is_connected=True, connection_info="second")
    current = {"manager": first}
    runtime = RadioRuntime(lambda: current["manager"])

    assert runtime.connection_info == "first"
    assert runtime.require_connected() == "mc1"

    current["manager"] = second

    assert runtime.connection_info == "second"
    assert runtime.require_connected() == "mc2"


def test_require_connected_preserves_http_semantics():
    runtime = RadioRuntime(
        _Manager(meshcore=None, is_connected=True, is_setup_in_progress=True),
    )
    with pytest.raises(HTTPException, match="Radio is initializing") as exc:
        runtime.require_connected()
    assert exc.value.status_code == 503

    runtime = RadioRuntime(_Manager(meshcore=None, is_connected=False, is_setup_in_progress=False))
    with pytest.raises(HTTPException, match="Radio not connected") as exc:
        runtime.require_connected()
    assert exc.value.status_code == 503


def test_require_connected_returns_fresh_meshcore_after_connectivity_check():
    old_meshcore = object()
    new_meshcore = object()

    class _SwappingManager:
        def __init__(self):
            self._meshcore = old_meshcore
            self.is_setup_in_progress = False

        @property
        def is_connected(self):
            self._meshcore = new_meshcore
            return True

        @property
        def meshcore(self):
            return self._meshcore

    runtime = RadioRuntime(_SwappingManager())

    assert runtime.require_connected() is new_meshcore


@pytest.mark.asyncio
async def test_radio_operation_delegates_to_current_manager():
    manager = _Manager(meshcore="meshcore", is_connected=True)
    runtime = RadioRuntime(manager)

    async with runtime.radio_operation("sync_contacts", pause_polling=True) as mc:
        assert mc == "meshcore"

    assert manager.calls == [("sync_contacts", {"pause_polling": True})]


@pytest.mark.asyncio
async def test_lifecycle_passthrough_methods_delegate_to_current_manager():
    manager = _Manager(meshcore="meshcore", is_connected=True)
    manager.start_connection_monitor = AsyncMock()
    manager.stop_connection_monitor = AsyncMock()
    manager.disconnect = AsyncMock()
    runtime = RadioRuntime(manager)

    await runtime.start_connection_monitor()
    await runtime.stop_connection_monitor()
    await runtime.disconnect()

    manager.start_connection_monitor.assert_awaited_once()
    manager.stop_connection_monitor.assert_awaited_once()
    manager.disconnect.assert_awaited_once()
