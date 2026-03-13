"""Tests for shared radio operation locking behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.radio import RadioDisconnectedError, RadioOperationBusyError, radio_manager
from app.radio_sync import is_polling_paused
from app.services.radio_runtime import RadioRuntime


def _runtime(manager):
    return RadioRuntime(lambda: manager)


@pytest.fixture(autouse=True)
def reset_radio_operation_state():
    """Reset shared radio operation lock state before/after each test."""
    prev_meshcore = radio_manager._meshcore
    radio_manager._operation_lock = None
    # Default to a non-None MagicMock so radio_operation() doesn't raise
    # RadioDisconnectedError for tests that only exercise locking.
    radio_manager._meshcore = MagicMock()

    import app.radio_sync as radio_sync

    radio_sync._polling_pause_count = 0
    yield
    radio_manager._operation_lock = None
    radio_manager._meshcore = prev_meshcore
    radio_sync._polling_pause_count = 0


class TestRadioOperationLock:
    """Validate shared radio operation lock semantics."""

    @pytest.mark.asyncio
    async def test_non_blocking_fails_when_lock_held_by_other_task(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with radio_manager.radio_operation("holder"):
                started.set()
                await release.wait()

        holder_task = asyncio.create_task(holder())
        await started.wait()

        with pytest.raises(RadioOperationBusyError):
            async with radio_manager.radio_operation("contender", blocking=False):
                pass

        release.set()
        await holder_task

    @pytest.mark.asyncio
    async def test_blocking_waits_and_acquires_after_release(self):
        holder_entered = asyncio.Event()
        holder_release = asyncio.Event()
        contender_entered = asyncio.Event()
        order: list[str] = []

        async def holder():
            async with radio_manager.radio_operation("holder"):
                order.append("holder_enter")
                holder_entered.set()
                await holder_release.wait()
                order.append("holder_exit")

        async def contender():
            await holder_entered.wait()
            async with radio_manager.radio_operation("contender"):
                order.append("contender_enter")
                contender_entered.set()

        holder_task = asyncio.create_task(holder())
        contender_task = asyncio.create_task(contender())

        await holder_entered.wait()
        await asyncio.sleep(0.02)
        assert not contender_entered.is_set()

        holder_release.set()
        await asyncio.wait_for(contender_entered.wait(), timeout=1.0)

        await holder_task
        await contender_task
        assert order == ["holder_enter", "holder_exit", "contender_enter"]

    @pytest.mark.asyncio
    async def test_suspend_auto_fetch_stops_and_restarts(self):
        mc = MagicMock()
        mc.stop_auto_message_fetching = AsyncMock()
        mc.start_auto_message_fetching = AsyncMock()
        radio_manager._meshcore = mc

        async with radio_manager.radio_operation(
            "auto_fetch_toggle",
            suspend_auto_fetch=True,
        ):
            pass

        mc.stop_auto_message_fetching.assert_awaited_once()
        mc.start_auto_message_fetching.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_released_when_auto_fetch_restart_is_cancelled(self):
        mc = MagicMock()
        mc.stop_auto_message_fetching = AsyncMock()
        mc.start_auto_message_fetching = AsyncMock(side_effect=asyncio.CancelledError())
        radio_manager._meshcore = mc

        with pytest.raises(asyncio.CancelledError):
            async with radio_manager.radio_operation(
                "cancelled_restart",
                suspend_auto_fetch=True,
            ):
                pass

        async with radio_manager.radio_operation("after_cancel", blocking=False):
            pass

    @pytest.mark.asyncio
    async def test_pause_polling_toggles_state(self):
        assert not is_polling_paused()

        async with radio_manager.radio_operation("pause_polling", pause_polling=True):
            assert is_polling_paused()

        assert not is_polling_paused()


class TestRadioOperationYield:
    """Validate that radio_operation() yields the current meshcore instance."""

    @pytest.mark.asyncio
    async def test_radio_operation_yields_current_meshcore(self):
        """The yielded value is the current _meshcore at lock-acquisition time."""
        mc = MagicMock()
        radio_manager._meshcore = mc

        async with radio_manager.radio_operation("test_yield") as yielded:
            assert yielded is mc

    @pytest.mark.asyncio
    async def test_radio_operation_raises_when_disconnected_after_lock(self):
        """RadioDisconnectedError is raised when _meshcore is None after acquiring the lock."""
        radio_manager._meshcore = None

        with pytest.raises(RadioDisconnectedError):
            async with radio_manager.radio_operation("test_disconnected"):
                pass  # pragma: no cover

        # Lock must be released even after the error
        radio_manager._meshcore = MagicMock()
        async with radio_manager.radio_operation("after_error", blocking=False):
            pass

    @pytest.mark.asyncio
    async def test_radio_operation_yields_fresh_reference_after_swap(self):
        """If _meshcore is swapped between pre-check and lock acquisition,
        the yielded value is the new (current) instance, not the old one."""
        old_mc = MagicMock(name="old")
        new_mc = MagicMock(name="new")

        # Start with old_mc
        radio_manager._meshcore = old_mc

        # Simulate a reconnect swapping _meshcore before the caller enters the block
        radio_manager._meshcore = new_mc

        async with radio_manager.radio_operation("test_swap") as yielded:
            assert yielded is new_mc
            assert yielded is not old_mc


class TestRequireConnected:
    """Test the require_connected() FastAPI dependency."""

    def test_raises_503_when_setup_in_progress(self):
        """HTTPException 503 is raised when radio is connected but setup is still in progress."""
        from fastapi import HTTPException

        from app.dependencies import require_connected

        manager = MagicMock()
        manager.is_connected = True
        manager.meshcore = MagicMock()
        manager.is_setup_in_progress = True
        with patch("app.dependencies.radio_manager", _runtime(manager)):
            with pytest.raises(HTTPException) as exc_info:
                require_connected()

            assert exc_info.value.status_code == 503
            assert "initializing" in exc_info.value.detail.lower()

    def test_raises_503_when_not_connected(self):
        """HTTPException 503 is raised when radio is not connected."""
        from fastapi import HTTPException

        from app.dependencies import require_connected

        manager = MagicMock()
        manager.is_setup_in_progress = False
        manager.is_connected = False
        manager.meshcore = None
        with patch("app.dependencies.radio_manager", _runtime(manager)):
            with pytest.raises(HTTPException) as exc_info:
                require_connected()

            assert exc_info.value.status_code == 503

    def test_returns_meshcore_when_connected_and_setup_complete(self):
        """Returns meshcore instance when radio is connected and setup is complete."""
        from app.dependencies import require_connected

        mock_mc = MagicMock()
        manager = MagicMock()
        manager.is_setup_in_progress = False
        manager.is_connected = True
        manager.meshcore = mock_mc
        with patch("app.dependencies.radio_manager", _runtime(manager)):
            result = require_connected()

        assert result is mock_mc
