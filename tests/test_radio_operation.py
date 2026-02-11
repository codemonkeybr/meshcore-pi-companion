"""Tests for shared radio operation locking behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.radio import RadioOperationBusyError, radio_manager
from app.radio_sync import is_polling_paused


@pytest.fixture(autouse=True)
def reset_radio_operation_state():
    """Reset shared radio operation lock state before/after each test."""
    prev_meshcore = radio_manager._meshcore
    radio_manager._operation_lock = None
    radio_manager._meshcore = None

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
