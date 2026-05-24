"""Tests for radio_sync module.

These tests verify the polling pause mechanism, radio time sync,
contact/channel sync operations, and default channel management.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from meshcore import EventType
from meshcore.events import Event

import app.radio_sync as radio_sync
from app.radio import RadioManager, radio_manager
from app.radio_sync import (
    _enable_autoevict_on_radio,
    _message_poll_loop,
    _periodic_advert_loop,
    _periodic_sync_loop,
    audit_channel_send_cache,
    ensure_contact_on_radio,
    is_polling_paused,
    pause_polling,
    sync_and_offload_all,
    sync_radio_time,
    sync_recent_contacts_to_radio,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)


@pytest.fixture(autouse=True)
def reset_sync_state():
    """Reset polling pause state, sync timestamp, and radio_manager before/after each test."""
    prev_mc = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    prev_max_channels = radio_manager.max_channels
    prev_connection_info = radio_manager._connection_info
    prev_slot_by_key = radio_manager._channel_slot_by_key.copy()
    prev_key_by_slot = radio_manager._channel_key_by_slot.copy()
    prev_pending_channel_key_by_slot = radio_manager._pending_message_channel_key_by_slot.copy()
    prev_contact_reconcile_task = radio_sync._contact_reconcile_task

    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0
    yield
    if (
        radio_sync._contact_reconcile_task is not None
        and radio_sync._contact_reconcile_task is not prev_contact_reconcile_task
        and not radio_sync._contact_reconcile_task.done()
    ):
        radio_sync._contact_reconcile_task.cancel()
    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0
    radio_sync._contact_reconcile_task = prev_contact_reconcile_task
    radio_manager._meshcore = prev_mc
    radio_manager._operation_lock = prev_lock
    radio_manager.max_channels = prev_max_channels
    radio_manager._connection_info = prev_connection_info
    radio_manager._channel_slot_by_key = prev_slot_by_key
    radio_manager._channel_key_by_slot = prev_key_by_slot
    radio_manager._pending_message_channel_key_by_slot = prev_pending_channel_key_by_slot


KEY_A = "aa" * 32
KEY_B = "bb" * 32


async def _insert_contact(
    public_key=KEY_A,
    name="Alice",
    on_radio=False,
    contact_type=0,
    flags=0,
    last_contacted=None,
    last_advert=None,
    direct_path=None,
    direct_path_len=-1,
    direct_path_hash_mode=-1,
):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
            "flags": flags,
            "direct_path": direct_path,
            "direct_path_len": direct_path_len,
            "direct_path_hash_mode": direct_path_hash_mode,
            "last_advert": last_advert,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": on_radio,
            "last_contacted": last_contacted,
        }
    )


class TestPollingPause:
    """Test the polling pause mechanism."""

    def test_initially_not_paused(self):
        """Polling is not paused by default."""
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_pause_polling_pauses(self):
        """pause_polling context manager pauses polling."""
        assert not is_polling_paused()

        async with pause_polling():
            assert is_polling_paused()

        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_nested_pause_stays_paused(self):
        """Nested pause_polling contexts keep polling paused until all exit."""
        assert not is_polling_paused()

        async with pause_polling():
            assert is_polling_paused()

            async with pause_polling():
                assert is_polling_paused()

            # Still paused - outer context active
            assert is_polling_paused()

        # Now unpaused - all contexts exited
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_pause_resumes_on_exception(self):
        """Polling resumes even if exception occurs in context."""
        try:
            async with pause_polling():
                assert is_polling_paused()
                raise ValueError("Test error")
        except ValueError:
            pass

        # Should be unpaused despite exception
        assert not is_polling_paused()

    @pytest.mark.asyncio
    async def test_nested_pause_resumes_correctly_on_inner_exception(self):
        """Nested contexts handle exceptions correctly."""
        async with pause_polling():
            try:
                async with pause_polling():
                    assert is_polling_paused()
                    raise ValueError("Inner error")
            except ValueError:
                pass

            # Outer context still active
            assert is_polling_paused()

        # All contexts exited
        assert not is_polling_paused()


class TestSyncRadioTime:
    """Test the radio time sync function."""

    @pytest.fixture(autouse=True)
    def _reset_reboot_flag(self):
        """Reset the module-level reboot guard between tests."""
        import app.radio_sync as _mod

        _mod._clock_reboot_attempted = False
        prev_wrap = _mod.settings.clowntown_do_clock_wraparound
        _mod.settings.clowntown_do_clock_wraparound = False
        yield
        _mod._clock_reboot_attempted = False
        _mod.settings.clowntown_do_clock_wraparound = prev_wrap

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        """sync_radio_time returns True when time is set successfully."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(return_value=Event(EventType.OK, {}))

        result = await sync_radio_time(mock_mc)

        assert result is True
        mock_mc.commands.set_time.assert_called_once()
        # Verify timestamp is reasonable (within last few seconds)
        call_args = mock_mc.commands.set_time.call_args[0][0]
        import time

        assert abs(call_args - int(time.time())) < 5

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        """sync_radio_time returns False and doesn't raise on error."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(side_effect=Exception("Radio error"))

        result = await sync_radio_time(mock_mc)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_firmware_rejects_and_reboots(self):
        """sync_radio_time reboots radio on first rejection with significant skew."""
        import time as _time

        radio_time = int(_time.time()) + 86400  # radio is 1 day ahead
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(
            return_value=Event(EventType.CURRENT_TIME, {"time": radio_time})
        )
        mock_mc.commands.reboot = AsyncMock()

        result = await sync_radio_time(mock_mc)

        assert result is False
        mock_mc.commands.get_time.assert_called_once()
        mock_mc.commands.reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_wraparound_can_fix_future_skew_before_normal_set(self):
        """Experimental wraparound retries time sync before the reboot fallback."""
        import app.radio_sync as _mod

        _mod.settings.clowntown_do_clock_wraparound = True

        mock_mc = MagicMock()
        mock_mc.commands.get_time = AsyncMock(
            side_effect=[
                Event(EventType.CURRENT_TIME, {"time": 2000}),
                Event(EventType.CURRENT_TIME, {"time": 1}),
            ]
        )
        mock_mc.commands.set_time = AsyncMock(
            side_effect=[
                Event(EventType.OK, {}),
                Event(EventType.OK, {}),
            ]
        )
        mock_mc.commands.reboot = AsyncMock()

        with (
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
            patch("app.radio_sync.time.time", return_value=1000),
            patch("app.radio_sync.time.monotonic", side_effect=[0.0, 0.0]),
        ):
            result = await sync_radio_time(mock_mc)

        assert result is True
        assert mock_mc.commands.set_time.call_args_list == [
            call(0xFFFFFFFF),
            call(1000),
        ]
        assert mock_mc.commands.get_time.call_count == 2
        mock_mc.commands.reboot.assert_not_called()

    @pytest.mark.asyncio
    async def test_wraparound_failure_falls_back_to_reboot(self):
        """A failed experimental wraparound still uses the existing reboot recovery."""
        import app.radio_sync as _mod

        _mod.settings.clowntown_do_clock_wraparound = True

        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(
            side_effect=[
                Event(EventType.CURRENT_TIME, {"time": 2000}),
                Event(EventType.CURRENT_TIME, {"time": 2000}),
            ]
        )
        mock_mc.commands.reboot = AsyncMock()

        with (
            patch("app.radio_sync.time.time", return_value=1000),
            patch("app.radio_sync._attempt_clock_wraparound", new=AsyncMock(return_value=False)),
        ):
            result = await sync_radio_time(mock_mc)

        assert result is False
        mock_mc.commands.reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_reboot_twice(self):
        """Second rejection logs hardware RTC warning instead of rebooting."""
        import time as _time

        import app.radio_sync as _mod

        _mod._clock_reboot_attempted = True  # simulate prior reboot

        radio_time = int(_time.time()) + 86400
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(
            return_value=Event(EventType.CURRENT_TIME, {"time": radio_time})
        )
        mock_mc.commands.reboot = AsyncMock()

        result = await sync_radio_time(mock_mc)

        assert result is False
        mock_mc.commands.reboot.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_rejected_and_get_time_fails(self):
        """sync_radio_time reboots even when get_time fails (unknown skew)."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(side_effect=Exception("timeout"))
        mock_mc.commands.reboot = AsyncMock()

        result = await sync_radio_time(mock_mc)

        assert result is False
        mock_mc.commands.reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_reboot_for_small_skew(self):
        """No reboot when radio is only slightly ahead (within tolerance)."""
        import time as _time

        radio_time = int(_time.time()) + 5  # only 5 seconds ahead
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(
            return_value=Event(EventType.CURRENT_TIME, {"time": radio_time})
        )
        mock_mc.commands.reboot = AsyncMock()

        result = await sync_radio_time(mock_mc)

        assert result is False
        mock_mc.commands.reboot.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_failures_log_debug_instead_of_warning(self, caplog):
        """Periodic syncs should not keep emitting warning-level clock skew logs."""
        import time as _time

        radio_time = int(_time.time()) + 86400
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock(
            return_value=Event(EventType.ERROR, {"reason": "illegal_arg"})
        )
        mock_mc.commands.get_time = AsyncMock(
            return_value=Event(EventType.CURRENT_TIME, {"time": radio_time})
        )
        mock_mc.commands.reboot = AsyncMock()

        with caplog.at_level("DEBUG"):
            result = await sync_radio_time(mock_mc, warn_on_failure=False)

        assert result is False
        assert "Radio rejected time sync:" in caplog.text
        assert not [
            rec
            for rec in caplog.records
            if rec.levelname == "WARNING" and "Radio rejected time sync:" in rec.message
        ]


class TestSyncRecentContactsToRadio:
    """Test the sync_recent_contacts_to_radio function."""

    @pytest.mark.asyncio
    async def test_loads_favorite_contacts_not_on_radio(self, test_db):
        """Favorite contacts not on radio are added via add_contact."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)
        await ContactRepository.set_favorite(KEY_A, True)
        await ContactRepository.set_favorite(KEY_B, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2

    @pytest.mark.asyncio
    async def test_fills_remaining_slots_with_dm_active_then_advertised(self, test_db):
        """Fill order is favorites, then DM-active contacts, then recent adverts."""
        await _insert_contact(KEY_A, "Alice")
        await _insert_contact(KEY_B, "Bob")
        await _insert_contact("cc" * 32, "Carol")
        await _insert_contact("dd" * 32, "Dave", last_advert=3000)
        await _insert_contact("ee" * 32, "Eve", last_advert=2500)

        # Create DM activity for Alice (oldest), Bob (most recent), Carol (middle)
        for key, ts in [(KEY_A, 100), (KEY_B, 2000), ("cc" * 32, 1000)]:
            await test_db.conn.execute(
                "INSERT INTO messages (type, conversation_key, text, received_at) VALUES ('PRIV', ?, 'hi', ?)",
                (key, ts),
            )
        await test_db.conn.commit()

        await AppSettingsRepository.update(max_radio_contacts=5)
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 4
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        # Alice (favorite), then Bob & Carol (DM-active, most recent first), then Dave (advert)
        assert loaded_keys == [KEY_A, KEY_B, "cc" * 32, "dd" * 32]

    @pytest.mark.asyncio
    async def test_favorites_can_exceed_non_favorite_refill_target(self, test_db):
        """Favorites are reloaded even when they exceed the 80% background refill target."""
        favorite_keys = ["aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32]
        for index, key in enumerate(favorite_keys):
            await _insert_contact(key, f"Favorite{index}", last_contacted=2000 - index)

        await AppSettingsRepository.update(max_radio_contacts=4)
        for key in favorite_keys:
            await ContactRepository.set_favorite(key, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 4
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == favorite_keys


class TestSyncAndOffloadAll:
    """Test session-local contact radio residency reset behavior."""

    @pytest.mark.asyncio
    async def test_clears_stale_contact_on_radio_flags_before_background_reconcile(self, test_db):
        await _insert_contact(KEY_A, "Alice", on_radio=True)
        await _insert_contact(KEY_B, "Bob", on_radio=True)

        mock_mc = MagicMock()

        with (
            patch(
                "app.radio_sync.sync_contacts_from_radio",
                new=AsyncMock(return_value={"synced": 0, "radio_contacts": {}}),
            ),
            patch(
                "app.radio_sync.sync_and_offload_channels",
                new=AsyncMock(return_value={"synced": 0, "cleared": 0}),
            ),
            patch("app.radio_sync.ensure_default_channels", new=AsyncMock()),
            patch(
                "app.radio_sync.start_background_contact_reconciliation",
            ),
        ):
            await sync_and_offload_all(mock_mc)

        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice is not None and alice.on_radio is False
        assert bob is not None and bob.on_radio is False

    @pytest.mark.asyncio
    async def test_starts_background_contact_reconcile_with_radio_snapshot(self, test_db):
        mock_mc = MagicMock()
        radio_contacts = {KEY_A: {"public_key": KEY_A}}

        with (
            patch(
                "app.radio_sync.sync_contacts_from_radio",
                new=AsyncMock(return_value={"synced": 1, "radio_contacts": radio_contacts}),
            ),
            patch(
                "app.radio_sync.sync_and_offload_channels",
                new=AsyncMock(return_value={"synced": 0, "cleared": 0}),
            ),
            patch("app.radio_sync.ensure_default_channels", new=AsyncMock()),
            patch("app.radio_sync.start_background_contact_reconciliation") as mock_start,
        ):
            result = await sync_and_offload_all(mock_mc)

        mock_start.assert_called_once_with(
            initial_radio_contacts=radio_contacts, expected_mc=mock_mc, autoevict=False
        )
        assert result["contact_reconcile_started"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_snapshot_reconcile_when_autoevict_enable_fails(self, test_db):
        mock_mc = MagicMock()
        radio_contacts = {KEY_A: {"public_key": KEY_A}}

        with (
            patch.object(radio_sync.settings, "load_with_autoevict", True),
            patch(
                "app.radio_sync._enable_autoevict_on_radio",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.radio_sync.sync_contacts_from_radio",
                new=AsyncMock(return_value={"synced": 1, "radio_contacts": radio_contacts}),
            ),
            patch(
                "app.radio_sync.sync_and_offload_channels",
                new=AsyncMock(return_value={"synced": 0, "cleared": 0}),
            ),
            patch("app.radio_sync.ensure_default_channels", new=AsyncMock()),
            patch("app.radio_sync.start_background_contact_reconciliation") as mock_start,
        ):
            result = await sync_and_offload_all(mock_mc)

        mock_start.assert_called_once_with(
            initial_radio_contacts=radio_contacts,
            expected_mc=mock_mc,
            autoevict=False,
        )
        assert result["contact_reconcile_started"] is True

    @pytest.mark.asyncio
    async def test_autoevict_success_passes_flag_to_reconcile(self, test_db):
        mock_mc = MagicMock()
        radio_contacts = {KEY_A: {"public_key": KEY_A}}

        with (
            patch.object(radio_sync.settings, "load_with_autoevict", True),
            patch(
                "app.radio_sync._enable_autoevict_on_radio",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.radio_sync.sync_contacts_from_radio",
                new=AsyncMock(return_value={"synced": 1, "radio_contacts": radio_contacts}),
            ),
            patch(
                "app.radio_sync.sync_and_offload_channels",
                new=AsyncMock(return_value={"synced": 0, "cleared": 0}),
            ),
            patch("app.radio_sync.ensure_default_channels", new=AsyncMock()),
            patch("app.radio_sync.start_background_contact_reconciliation") as mock_start,
        ):
            result = await sync_and_offload_all(mock_mc)

        mock_start.assert_called_once_with(
            initial_radio_contacts=radio_contacts,
            expected_mc=mock_mc,
            autoevict=True,
        )
        assert result["contact_reconcile_started"] is True

    @pytest.mark.asyncio
    async def test_best_effort_reconcile_when_snapshot_fails(self, test_db):
        """When sync_contacts_from_radio errors, reconcile still starts with empty snapshot."""
        mock_mc = MagicMock()

        with (
            patch(
                "app.radio_sync.sync_contacts_from_radio",
                new=AsyncMock(return_value={"synced": 0, "radio_contacts": {}, "error": "timeout"}),
            ),
            patch(
                "app.radio_sync.sync_and_offload_channels",
                new=AsyncMock(return_value={"synced": 0, "cleared": 0}),
            ),
            patch("app.radio_sync.ensure_default_channels", new=AsyncMock()),
            patch("app.radio_sync.start_background_contact_reconciliation") as mock_start,
            patch("app.radio_sync.broadcast_error") as mock_broadcast,
        ):
            result = await sync_and_offload_all(mock_mc)

        mock_start.assert_called_once_with(
            initial_radio_contacts={},
            expected_mc=mock_mc,
            autoevict=False,
        )
        assert result["contact_reconcile_started"] is True
        mock_broadcast.assert_called_once()
        assert "best-effort" in mock_broadcast.call_args.args[1]

    @pytest.mark.asyncio
    async def test_advert_fill_skips_repeaters(self, test_db):
        """Recent advert fallback only considers non-repeaters."""
        await _insert_contact(KEY_A, "Alice", last_advert=3000, contact_type=2)
        await _insert_contact(KEY_B, "Bob", last_advert=2000, contact_type=1)

        await AppSettingsRepository.update(max_radio_contacts=1)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_B

    @pytest.mark.asyncio
    async def test_duplicate_favorite_not_loaded_twice(self, test_db):
        """Duplicate favorite entries still load the contact only once."""
        await _insert_contact(KEY_A, "Alice")
        await _insert_contact(KEY_B, "Bob")

        # Bob has DM activity so he appears in tier 2
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES ('PRIV', ?, 'hi', 1000)",
            (KEY_B,),
        )
        await test_db.conn.commit()

        await AppSettingsRepository.update(max_radio_contacts=2)
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B]

    @pytest.mark.asyncio
    async def test_skips_contacts_already_on_radio(self, test_db):
        """Contacts already on radio are counted but not re-added."""
        await _insert_contact(KEY_A, "Alice", on_radio=False)
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # Found
        mock_mc.commands.add_contact = AsyncMock()

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["already_on_radio"] == 1
        mock_mc.commands.add_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttled_when_called_quickly(self, test_db):
        """Second call within throttle window returns throttled result."""
        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)

        radio_manager._meshcore = mock_mc

        # First call succeeds
        result1 = await sync_recent_contacts_to_radio()
        assert "throttled" not in result1

        # Second call is throttled
        result2 = await sync_recent_contacts_to_radio()
        assert result2["throttled"] is True
        assert result2["loaded"] == 0

    @pytest.mark.asyncio
    async def test_force_bypasses_throttle(self, test_db):
        """force=True bypasses the throttle window."""
        mock_mc = MagicMock()

        radio_manager._meshcore = mock_mc

        # First call
        await sync_recent_contacts_to_radio()

        # Forced second call is not throttled
        result = await sync_recent_contacts_to_radio(force=True)
        assert "throttled" not in result

    @pytest.mark.asyncio
    async def test_not_connected_returns_error(self):
        """Returns error when radio is not connected."""
        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_add_failure(self, test_db):
        """Failed add_contact increments the failed counter."""
        await _insert_contact(KEY_A, "Alice")
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.ERROR
        mock_result.payload = {"error": "Radio full"}
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_add_contact_preserves_explicit_multibyte_hash_mode(self, test_db):
        """Radio offload uses the stored hash mode rather than inferring from path bytes."""
        await _insert_contact(
            KEY_A,
            "Alice",
            last_contacted=2000,
            direct_path="aa00bb00",
            direct_path_len=2,
            direct_path_hash_mode=1,
        )
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_A
        assert payload["out_path"] == "aa00bb00"
        assert payload["out_path_len"] == 2
        assert payload["out_path_hash_mode"] == 1

    @pytest.mark.asyncio
    async def test_add_contact_decodes_legacy_packed_path_len(self, test_db):
        """Legacy signed packed path bytes are normalized before add_contact."""
        await _insert_contact(
            KEY_A,
            "Alice",
            last_contacted=2000,
            direct_path="3f3f69de1c7b7e7662",
            direct_path_len=-125,
            direct_path_hash_mode=2,
        )
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["out_path"] == "3f3f69de1c7b7e7662"
        assert payload["out_path_len"] == 3
        assert payload["out_path_hash_mode"] == 2

    @pytest.mark.asyncio
    async def test_mc_param_bypasses_lock_acquisition(self, test_db):
        """When mc is passed, the function uses it directly without acquiring radio_operation.

        This tests the BUG-1 fix: sync_and_offload_all already holds the lock,
        so it passes mc directly to avoid deadlock (asyncio.Lock is not reentrant).
        """
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await ContactRepository.set_favorite(KEY_A, True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        # Make radio_operation raise if called — it should NOT be called
        # when mc is provided
        def radio_operation_should_not_be_called(*args, **kwargs):
            raise AssertionError("radio_operation should not be called when mc is passed")

        with patch.object(
            radio_manager, "radio_operation", side_effect=radio_operation_should_not_be_called
        ):
            result = await sync_recent_contacts_to_radio(mc=mock_mc)

        assert result["loaded"] == 1
        mock_mc.commands.add_contact.assert_called_once()

    @pytest.mark.asyncio
    async def test_mc_param_still_respects_throttle(self):
        """When mc is passed but throttle is active (not forced), it should still return throttled."""
        mock_mc = MagicMock()

        # First call to set _last_contact_sync
        radio_manager._meshcore = mock_mc
        await sync_recent_contacts_to_radio()

        # Second call with mc= but no force — should still be throttled
        result = await sync_recent_contacts_to_radio(mc=mock_mc)
        assert result["throttled"] is True
        assert result["loaded"] == 0

    @pytest.mark.asyncio
    async def test_uses_post_lock_meshcore_after_swap(self, test_db):
        """If _meshcore is swapped between pre-check and lock acquisition,
        the function uses the new (post-lock) instance, not the stale one."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await ContactRepository.set_favorite(KEY_A, True)

        old_mc = MagicMock(name="old_mc")
        new_mc = MagicMock(name="new_mc")
        new_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        new_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        # Pre-check sees old_mc (truthy, passes is_connected guard)
        radio_manager._meshcore = old_mc
        # Simulate reconnect swapping _meshcore before lock acquisition
        radio_manager._meshcore = new_mc

        result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 1
        # new_mc was used, not old_mc
        new_mc.commands.add_contact.assert_called_once()
        old_mc.commands.add_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_contact_on_radio_loads_single_contact_even_when_not_favorited(
        self, test_db
    ):
        """Targeted sync loads one contact without needing it in favorites."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        result = await ensure_contact_on_radio(KEY_A, force=True)

        assert result["loaded"] == 1
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_A


class TestEnableAutoevictOnRadio:
    """Test _enable_autoevict_on_radio read-modify-write flow."""

    @pytest.mark.asyncio
    async def test_sets_flag_when_not_already_set(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={"config": 0x00})
        )
        mc.commands.set_autoadd_config = AsyncMock(return_value=MagicMock(type=EventType.OK))

        result = await _enable_autoevict_on_radio(mc)

        assert result is True
        mc.commands.set_autoadd_config.assert_awaited_once_with(0x01)

    @pytest.mark.asyncio
    async def test_noop_when_already_enabled(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={"config": 0x01})
        )
        mc.commands.set_autoadd_config = AsyncMock()

        result = await _enable_autoevict_on_radio(mc)

        assert result is True
        mc.commands.set_autoadd_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preserves_other_flags(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={"config": 0x04})
        )
        mc.commands.set_autoadd_config = AsyncMock(return_value=MagicMock(type=EventType.OK))

        result = await _enable_autoevict_on_radio(mc)

        assert result is True
        mc.commands.set_autoadd_config.assert_awaited_once_with(0x05)

    @pytest.mark.asyncio
    async def test_returns_false_on_get_error(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload=None)
        )

        result = await _enable_autoevict_on_radio(mc)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_set_failure(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(
            return_value=MagicMock(type=EventType.OK, payload={"config": 0x00})
        )
        mc.commands.set_autoadd_config = AsyncMock(return_value=MagicMock(type=EventType.ERROR))

        result = await _enable_autoevict_on_radio(mc)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        mc = MagicMock()
        mc.commands.get_autoadd_config = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await _enable_autoevict_on_radio(mc)

        assert result is False


class TestBackgroundContactReconcile:
    """Test the yielding background contact reconcile loop."""

    @pytest.mark.asyncio
    async def test_rechecks_desired_set_before_deleting_contact(self, test_db):
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice is not None
        assert bob is not None

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_mc.commands.remove_contact = AsyncMock(return_value=MagicMock(type=EventType.OK))
        mock_mc.commands.add_contact = AsyncMock(return_value=MagicMock(type=EventType.OK))
        radio_manager._meshcore = mock_mc

        @asynccontextmanager
        async def _radio_operation(*args, **kwargs):
            del args, kwargs
            yield mock_mc

        with (
            patch.object(
                radio_sync.radio_manager,
                "radio_operation",
                side_effect=lambda *args, **kwargs: _radio_operation(*args, **kwargs),
            ),
            patch(
                "app.radio_sync.get_contacts_selected_for_radio_sync",
                side_effect=[[bob], [alice, bob], [alice, bob]],
            ),
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
        ):
            await radio_sync._reconcile_radio_contacts_in_background(
                initial_radio_contacts={KEY_A: {"public_key": KEY_A}},
                expected_mc=mock_mc,
            )

        mock_mc.commands.remove_contact.assert_not_called()
        mock_mc.commands.add_contact.assert_awaited_once()
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["public_key"] == KEY_B

    @pytest.mark.asyncio
    async def test_autoevict_blind_fill_readds_full_desired_set(self, test_db):
        await _insert_contact(KEY_A, "Alice", flags=0x01, last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice is not None
        assert bob is not None

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_mc.commands.remove_contact = AsyncMock(return_value=MagicMock(type=EventType.OK))
        mock_mc.commands.add_contact = AsyncMock(return_value=MagicMock(type=EventType.OK))
        radio_manager._meshcore = mock_mc

        @asynccontextmanager
        async def _radio_operation(*args, **kwargs):
            del args, kwargs
            yield mock_mc

        with (
            patch.object(
                radio_sync.radio_manager,
                "radio_operation",
                side_effect=lambda *args, **kwargs: _radio_operation(*args, **kwargs),
            ),
            patch("app.radio_sync.CONTACT_RECONCILE_BATCH_SIZE", 10),
            patch(
                "app.radio_sync.get_contacts_selected_for_radio_sync",
                side_effect=[[alice, bob], [alice, bob]],
            ),
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
        ):
            await radio_sync._reconcile_radio_contacts_in_background(
                initial_radio_contacts={KEY_A: {"public_key": KEY_A}},
                expected_mc=mock_mc,
                autoevict=True,
            )

        mock_mc.commands.remove_contact.assert_not_called()
        assert mock_mc.commands.add_contact.await_count == 2
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B]
        loaded_flags = [
            call.args[0]["flags"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_flags == [0, 0]

    @pytest.mark.asyncio
    async def test_autoevict_table_full_breaks_with_error(self, test_db):
        """TABLE_FULL during autoevict stops the loop and broadcasts an error."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        alice = await ContactRepository.get_by_key(KEY_A)
        assert alice is not None

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        table_full_result = MagicMock(type=EventType.ERROR, payload={"error_code": 3})
        mock_mc.commands.add_contact = AsyncMock(return_value=table_full_result)
        radio_manager._meshcore = mock_mc

        @asynccontextmanager
        async def _radio_operation(*args, **kwargs):
            del args, kwargs
            yield mock_mc

        with (
            patch.object(
                radio_sync.radio_manager,
                "radio_operation",
                side_effect=lambda *args, **kwargs: _radio_operation(*args, **kwargs),
            ),
            patch("app.radio_sync.CONTACT_RECONCILE_BATCH_SIZE", 10),
            patch(
                "app.radio_sync.get_contacts_selected_for_radio_sync",
                side_effect=[[alice], [alice]],
            ),
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
            patch("app.radio_sync.broadcast_error") as mock_broadcast,
        ):
            await radio_sync._reconcile_radio_contacts_in_background(
                initial_radio_contacts={},
                expected_mc=mock_mc,
                autoevict=True,
            )

        mock_broadcast.assert_called_once()
        assert "auto-evict" in mock_broadcast.call_args.args[1].lower()

    @pytest.mark.asyncio
    async def test_autoevict_retry_cap_stops_after_max_retries(self, test_db):
        """Autoevict gives up after _MAX_AUTOEVICT_RETRIES full passes with failures."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        alice = await ContactRepository.get_by_key(KEY_A)
        assert alice is not None

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        # Every add fails with a non-TABLE_FULL error
        fail_result = MagicMock(type=EventType.ERROR, payload={"error_code": 99})
        mock_mc.commands.add_contact = AsyncMock(return_value=fail_result)
        radio_manager._meshcore = mock_mc

        @asynccontextmanager
        async def _radio_operation(*args, **kwargs):
            del args, kwargs
            yield mock_mc

        call_count = 0

        async def _get_selected():
            nonlocal call_count
            call_count += 1
            return [alice]

        with (
            patch.object(
                radio_sync.radio_manager,
                "radio_operation",
                side_effect=lambda *args, **kwargs: _radio_operation(*args, **kwargs),
            ),
            patch("app.radio_sync.CONTACT_RECONCILE_BATCH_SIZE", 10),
            patch(
                "app.radio_sync.get_contacts_selected_for_radio_sync",
                side_effect=_get_selected,
            ),
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
        ):
            await radio_sync._reconcile_radio_contacts_in_background(
                initial_radio_contacts={},
                expected_mc=mock_mc,
                autoevict=True,
            )

        # 2 calls per iteration (pre-lock + in-lock), 3 retries = 6 calls,
        # plus 1 pre-lock call on the initial iteration = at most 8.
        # The key assertion: it terminates rather than looping forever.
        assert mock_mc.commands.add_contact.await_count <= 4
        assert call_count <= 8

    @pytest.mark.asyncio
    async def test_yields_radio_lock_every_two_contact_operations(self, test_db):
        await _insert_contact(KEY_A, "Alice", last_contacted=3000)
        await _insert_contact(KEY_B, "Bob", last_contacted=2000)
        extra_key = "cc" * 32
        await _insert_contact(extra_key, "Carol", last_contacted=1000)

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_mc.commands.remove_contact = AsyncMock(return_value=MagicMock(type=EventType.OK))
        mock_mc.commands.add_contact = AsyncMock()
        radio_manager._meshcore = mock_mc

        acquire_count = 0

        @asynccontextmanager
        async def _radio_operation(*args, **kwargs):
            del args, kwargs
            nonlocal acquire_count
            acquire_count += 1
            yield mock_mc

        with (
            patch.object(
                radio_sync.radio_manager,
                "radio_operation",
                side_effect=lambda *args, **kwargs: _radio_operation(*args, **kwargs),
            ),
            patch("app.radio_sync.get_contacts_selected_for_radio_sync", return_value=[]),
            patch("app.radio_sync.asyncio.sleep", new=AsyncMock()),
        ):
            await radio_sync._reconcile_radio_contacts_in_background(
                initial_radio_contacts={
                    KEY_A: {"public_key": KEY_A},
                    KEY_B: {"public_key": KEY_B},
                    extra_key: {"public_key": extra_key},
                },
                expected_mc=mock_mc,
            )

        assert acquire_count == 2
        assert mock_mc.commands.remove_contact.await_count == 3
        mock_mc.commands.add_contact.assert_not_called()


class TestSyncAndOffloadChannels:
    """Test sync_and_offload_channels: pull channels from radio, save to DB, clear from radio."""

    @pytest.mark.asyncio
    async def test_syncs_valid_channel_and_clears(self, test_db):
        """Valid channel is upserted to DB and cleared from radio."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#general",
            "channel_secret": bytes.fromhex("8B3387E9C5CDEA6AC9E5EDBAA115CD72"),
        }

        # All other slots return non-CHANNEL_INFO
        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [empty_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 1
        assert result["cleared"] == 1

        # Verify channel is in real DB
        channel = await ChannelRepository.get_by_key("8B3387E9C5CDEA6AC9E5EDBAA115CD72")
        assert channel is not None
        assert channel.name == "#general"
        assert channel.is_hashtag is True
        assert channel.on_radio is False

    @pytest.mark.asyncio
    async def test_skips_empty_channel_name(self):
        """Channels with empty names are skipped."""
        from app.radio_sync import sync_and_offload_channels

        empty_name_result = MagicMock()
        empty_name_result.type = EventType.CHANNEL_INFO
        empty_name_result.payload = {
            "channel_name": "",
            "channel_secret": bytes(16),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(
            side_effect=[empty_name_result] + [other_result] * 39
        )

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 0
        assert result["cleared"] == 0

    @pytest.mark.asyncio
    async def test_skips_channel_with_zero_key(self):
        """Channels with all-zero secret key are skipped."""
        from app.radio_sync import sync_and_offload_channels

        zero_key_result = MagicMock()
        zero_key_result.type = EventType.CHANNEL_INFO
        zero_key_result.payload = {
            "channel_name": "SomeChannel",
            "channel_secret": bytes(16),  # All zeros
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(
            side_effect=[zero_key_result] + [other_result] * 39
        )

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 0

    @pytest.mark.asyncio
    async def test_non_hashtag_channel_detected(self, test_db):
        """Channel without '#' prefix has is_hashtag=False."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "Public",
            "channel_secret": bytes.fromhex("8B3387E9C5CDEA6AC9E5EDBAA115CD72"),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [other_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        await sync_and_offload_channels(mock_mc)

        channel = await ChannelRepository.get_by_key("8B3387E9C5CDEA6AC9E5EDBAA115CD72")
        assert channel is not None
        assert channel.is_hashtag is False

    @pytest.mark.asyncio
    async def test_clears_channel_with_empty_name_and_zero_key(self, test_db):
        """Cleared channels are set with empty name and 16 zero bytes."""
        from app.radio_sync import sync_and_offload_channels

        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#test",
            "channel_secret": bytes.fromhex("AABBCCDD" * 4),
        }

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [other_result] * 39)

        clear_result = MagicMock()
        clear_result.type = EventType.OK
        mock_mc.commands.set_channel = AsyncMock(return_value=clear_result)

        await sync_and_offload_channels(mock_mc)

        mock_mc.commands.set_channel.assert_called_once_with(
            channel_idx=0,
            channel_name="",
            channel_secret=bytes(16),
        )

    @pytest.mark.asyncio
    async def test_handles_clear_failure_gracefully(self, test_db):
        """Failed set_channel logs warning but continues processing."""
        from app.radio_sync import sync_and_offload_channels

        channel_results = []
        for i in range(2):
            r = MagicMock()
            r.type = EventType.CHANNEL_INFO
            r.payload = {
                "channel_name": f"#ch{i}",
                "channel_secret": bytes([i + 1] * 16),
            }
            channel_results.append(r)

        other_result = MagicMock()
        other_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=channel_results + [other_result] * 38)

        fail_result = MagicMock()
        fail_result.type = EventType.ERROR
        fail_result.payload = {"error": "busy"}

        ok_result = MagicMock()
        ok_result.type = EventType.OK

        mock_mc.commands.set_channel = AsyncMock(side_effect=[fail_result, ok_result])

        result = await sync_and_offload_channels(mock_mc)

        assert result["synced"] == 2
        assert result["cleared"] == 1

    @pytest.mark.asyncio
    async def test_iterates_all_40_channel_slots(self):
        """All firmware-reported channel slots are checked."""
        from app.radio_sync import sync_and_offload_channels

        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=empty_result)
        radio_manager.max_channels = 8

        result = await sync_and_offload_channels(mock_mc)

        assert mock_mc.commands.get_channel.call_count == 8
        assert result["synced"] == 0
        assert result["cleared"] == 0

    @pytest.mark.asyncio
    async def test_channel_offload_resets_send_slot_cache(self):
        """Clearing radio channels should invalidate session-local send-slot reuse state."""
        from app.radio_sync import sync_and_offload_channels

        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=empty_result)
        radio_manager.max_channels = 2
        radio_manager.note_channel_slot_loaded("AA" * 16, 0)

        await sync_and_offload_channels(mock_mc)

        assert radio_manager.get_cached_channel_slot("AA" * 16) is None

    @pytest.mark.asyncio
    async def test_remembers_channel_slot_for_pending_message_recovery(self, test_db):
        """Offload snapshots slot-to-key mapping for the later startup drain."""
        from app.radio_sync import sync_and_offload_channels

        channel_key = "11" * 16
        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#queued",
            "channel_secret": bytes.fromhex(channel_key),
        }

        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(side_effect=[channel_result] + [empty_result] * 39)
        mock_mc.commands.set_channel = AsyncMock(return_value=MagicMock(type=EventType.OK))

        await sync_and_offload_channels(mock_mc)

        assert radio_manager.get_pending_message_channel_key(0) == channel_key.upper()


class TestPendingChannelMessageFallback:
    """Queued CHANNEL_MSG_RECV events should be persisted instead of dropped."""

    @pytest.mark.asyncio
    async def test_drain_pending_messages_uses_snapshotted_slot_mapping_after_offload(
        self, test_db
    ):
        """Startup drain can still store room traffic even after slots were cleared."""
        from app.radio_sync import drain_pending_messages

        channel_key = "22" * 16
        await ChannelRepository.upsert(key=channel_key, name="#queued")
        radio_manager.remember_pending_message_channel_slot(channel_key, 3)

        channel_message = MagicMock()
        channel_message.type = EventType.CHANNEL_MSG_RECV
        channel_message.payload = {
            "channel_idx": 3,
            "text": "Alice: hello from queue",
            "sender_timestamp": 1700000000,
            "txt_type": 0,
            "path": "aabb",
            "path_len": 2,
        }

        no_more = MagicMock()
        no_more.type = EventType.NO_MORE_MSGS
        no_more.payload = {}

        empty_slot = MagicMock()
        empty_slot.type = EventType.ERROR
        empty_slot.payload = {"error": "slot empty"}

        mock_mc = MagicMock()
        mock_mc.commands.get_msg = AsyncMock(side_effect=[channel_message, no_more])
        mock_mc.commands.get_channel = AsyncMock(return_value=empty_slot)

        with patch("app.radio_sync.broadcast_event") as mock_broadcast:
            drained = await drain_pending_messages(mock_mc)

        assert drained == 1
        stored = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
        assert len(stored) == 1
        assert stored[0].text == "Alice: hello from queue"
        assert stored[0].sender_name == "Alice"
        assert stored[0].conversation_key == channel_key
        assert stored[0].paths is not None
        assert stored[0].paths[0].path == "aabb"

        mock_broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_for_messages_stores_first_pending_channel_message(self, test_db):
        """Single-pass polling stores the first queued channel message before draining."""
        from app.radio_sync import poll_for_messages

        channel_key = "33" * 16
        channel_result = MagicMock()
        channel_result.type = EventType.CHANNEL_INFO
        channel_result.payload = {
            "channel_name": "#poll",
            "channel_secret": bytes.fromhex(channel_key),
        }

        channel_message = MagicMock()
        channel_message.type = EventType.CHANNEL_MSG_RECV
        channel_message.payload = {
            "channel_idx": 1,
            "text": "Bob: polled message",
            "sender_timestamp": 1700000010,
            "txt_type": 0,
        }

        no_more = MagicMock()
        no_more.type = EventType.NO_MORE_MSGS
        no_more.payload = {}

        mock_mc = MagicMock()
        mock_mc.commands.get_msg = AsyncMock(side_effect=[channel_message, no_more])
        mock_mc.commands.get_channel = AsyncMock(return_value=channel_result)

        with patch("app.radio_sync.broadcast_event"):
            count = await poll_for_messages(mock_mc)

        assert count == 1
        stored = await MessageRepository.get_all(msg_type="CHAN", conversation_key=channel_key)
        assert len(stored) == 1
        assert stored[0].text == "Bob: polled message"


class TestEnsureDefaultChannels:
    """Test ensure_default_channels: create/fix the Public channel."""

    PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

    @pytest.mark.asyncio
    async def test_creates_public_channel_when_missing(self, test_db):
        """Public channel is created when it does not exist."""
        from app.radio_sync import ensure_default_channels

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel is not None
        assert channel.name == "Public"
        assert channel.is_hashtag is False
        assert channel.on_radio is False

    @pytest.mark.asyncio
    async def test_fixes_public_channel_with_wrong_name(self, test_db):
        """Public channel name is corrected when it exists with wrong name."""
        from app.radio_sync import ensure_default_channels

        # Pre-insert with wrong name
        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="public",  # Wrong case
            is_hashtag=False,
            on_radio=True,
        )

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.name == "Public"
        assert channel.on_radio is True  # Preserves existing on_radio state

    @pytest.mark.asyncio
    async def test_no_op_when_public_channel_exists_correctly(self, test_db):
        """No upsert when Public channel already exists with correct name."""
        from app.radio_sync import ensure_default_channels

        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="Public",
            is_hashtag=False,
            on_radio=False,
        )

        await ensure_default_channels()

        # Still exists and unchanged
        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.name == "Public"

    @pytest.mark.asyncio
    async def test_preserves_on_radio_state_when_fixing_name(self, test_db):
        """existing.on_radio is passed through when fixing the channel name."""
        from app.radio_sync import ensure_default_channels

        await ChannelRepository.upsert(
            key=self.PUBLIC_KEY,
            name="Pub",
            is_hashtag=False,
            on_radio=True,
        )

        await ensure_default_channels()

        channel = await ChannelRepository.get_by_key(self.PUBLIC_KEY)
        assert channel.on_radio is True


# ---------------------------------------------------------------------------
# Background loop race-condition regression tests
#
# Each loop uses radio_operation(blocking=False) which can raise
# RadioDisconnectedError (disconnect between pre-check and lock) or
# RadioOperationBusyError (lock already held).  These tests verify the
# loops handle both gracefully and that the lock-scoped mc is what gets
# forwarded to helper functions (the R1 fix).
# ---------------------------------------------------------------------------


def _make_connected_manager() -> tuple[RadioManager, MagicMock]:
    """Create a RadioManager with a mock MeshCore that reports is_connected=True."""
    rm = RadioManager()
    mock_mc = MagicMock(name="lock_scoped_mc")
    mock_mc.is_connected = True
    mock_mc.stop_auto_message_fetching = AsyncMock()
    mock_mc.start_auto_message_fetching = AsyncMock()
    rm._meshcore = mock_mc
    return rm, mock_mc


def _disconnect_on_acquire(rm: RadioManager):
    """Monkey-patch rm so _meshcore is set to None right after the lock is acquired.

    This simulates the exact race: is_connected pre-check passes, but by the
    time radio_operation() checks _meshcore post-lock, a reconnect has set it
    to None → RadioDisconnectedError.
    """
    original = rm._acquire_operation_lock

    async def _acquire_then_disconnect(name, *, blocking):
        await original(name, blocking=blocking)
        rm._meshcore = None

    rm._acquire_operation_lock = _acquire_then_disconnect


async def _pre_hold_lock(rm: RadioManager) -> asyncio.Lock:
    """Pre-acquire the operation lock so non-blocking callers get RadioOperationBusyError."""
    if rm._operation_lock is None:
        rm._operation_lock = asyncio.Lock()
    await rm._operation_lock.acquire()
    return rm._operation_lock


def _sleep_controller(*, cancel_after: int = 2):
    """Return a (mock_sleep, calls) pair.

    mock_sleep returns normally for the first *cancel_after - 1* calls, then
    raises ``CancelledError`` to cleanly stop the infinite loop.
    """
    calls: list[float] = []

    async def _sleep(duration):
        calls.append(duration)
        if len(calls) >= cancel_after:
            raise asyncio.CancelledError()

    return _sleep, calls


class TestMessagePollLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _message_poll_loop."""

    @pytest.mark.asyncio
    async def test_uses_hourly_audit_interval_when_fallback_disabled(self):
        rm, _mc = _make_connected_manager()
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=1)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", False),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            await _message_poll_loop()

        assert sleep_calls == [3600]

    @pytest.mark.asyncio
    async def test_uses_fast_poll_interval_when_fallback_enabled(self):
        rm, _mc = _make_connected_manager()
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=1)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", True),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            await _message_poll_loop()

        assert sleep_calls == [10]

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks"),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
        ):
            await _message_poll_loop()

        mock_poll.assert_not_called()
        # Loop ran two iterations: first handled the error, second was cancelled
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and poll_for_messages is not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.cleanup_expired_acks"),
                patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
            ):
                await _message_poll_loop()
        finally:
            lock.release()

        mock_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        poll_for_messages — not a stale radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks"),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock) as mock_poll,
        ):
            await _message_poll_loop()

        mock_poll.assert_called_once_with(mock_mc)

    @pytest.mark.asyncio
    async def test_hourly_audit_crows_loudly_when_it_finds_hidden_messages(self):
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", False),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock, return_value=2),
            patch("app.radio_sync.broadcast_error") as mock_broadcast_error,
        ):
            await _message_poll_loop()

        mock_broadcast_error.assert_called_once_with(
            "A periodic poll task has discovered radio inconsistencies.",
            "Please check the logs for recommendations (search "
            "'MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK').",
        )

    @pytest.mark.asyncio
    async def test_fast_poll_logs_missed_messages_without_error_toast(self):
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("app.radio_sync.settings.enable_message_poll_fallback", True),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.poll_for_messages", new_callable=AsyncMock, return_value=2),
            patch("app.radio_sync.broadcast_error") as mock_broadcast_error,
        ):
            await _message_poll_loop()

        mock_broadcast_error.assert_not_called()


class TestChannelSendCacheAudit:
    """Verify session-local channel-slot reuse state is audited against the radio."""

    @pytest.mark.asyncio
    async def test_audit_channel_send_cache_accepts_matching_radio_state(self, test_db):
        chan_key = "ab" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        radio_manager.note_channel_slot_loaded(chan_key, 0)

        ok_result = MagicMock()
        ok_result.type = EventType.CHANNEL_INFO
        ok_result.payload = {
            "channel_name": "#flightless",
            "channel_secret": bytes.fromhex(chan_key),
        }

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=ok_result)

        with patch("app.radio_sync.broadcast_error") as mock_broadcast_error:
            assert await audit_channel_send_cache(mock_mc) is True

        mock_mc.commands.get_channel.assert_awaited_once_with(0)
        mock_broadcast_error.assert_not_called()
        assert radio_manager.get_cached_channel_slot(chan_key) == 0

    @pytest.mark.asyncio
    async def test_audit_channel_send_cache_resets_and_toasts_on_mismatch(self, test_db):
        chan_key = "cd" * 16
        await ChannelRepository.upsert(key=chan_key, name="#flightless")
        radio_manager.note_channel_slot_loaded(chan_key, 0)

        mismatch_result = MagicMock()
        mismatch_result.type = EventType.CHANNEL_INFO
        mismatch_result.payload = {
            "channel_name": "#elsewhere",
            "channel_secret": bytes.fromhex("ef" * 16),
        }

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=mismatch_result)

        with patch("app.radio_sync.broadcast_error") as mock_broadcast_error:
            assert await audit_channel_send_cache(mock_mc) is False

        mock_broadcast_error.assert_called_once()
        assert radio_manager.get_cached_channel_slot(chan_key) is None

    @pytest.mark.asyncio
    async def test_audit_channel_send_cache_skips_when_reuse_forced_off(self, test_db):
        chan_key = "ef" * 16
        radio_manager.note_channel_slot_loaded(chan_key, 0)
        mock_mc = MagicMock()

        with patch("app.radio.settings.force_channel_slot_reconfigure", True):
            assert await audit_channel_send_cache(mock_mc) is True

        mock_mc.commands.get_channel.assert_not_called()


class TestPeriodicAdvertLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _periodic_advert_loop."""

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        # Advert loop: sleep first, then work. Sleep 1 (loop top) passes,
        # work hits RadioDisconnectedError, next iteration sleep 2 cancels
        # cleanly via except CancelledError without an extra backoff sleep.
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
        ):
            await _periodic_advert_loop()

        mock_advert.assert_not_called()
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and send_advertisement is not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        # Sleep 1 (loop top) passes, work hits busy error, sleep 2 cancels.
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
            ):
                await _periodic_advert_loop()
        finally:
            lock.release()

        mock_advert.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        send_advertisement — not a stale radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        # Sleep 1 (loop top) passes through, work runs, sleep 2 cancels.
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock) as mock_advert,
        ):
            await _periodic_advert_loop()

        mock_advert.assert_called_once_with(mock_mc)


class TestPeriodicSyncLoopRaces:
    """Regression tests for disconnect/reconnect race paths in _periodic_sync_loop."""

    @pytest.mark.asyncio
    async def test_should_run_full_periodic_sync_at_trigger_threshold(self, test_db):
        """Occupancy at 95% of configured capacity triggers a full offload/reload."""
        from app.radio_sync import should_run_full_periodic_sync

        await AppSettingsRepository.update(max_radio_contacts=100)

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.NEW_CONTACT
        mock_result.payload = {f"{i:064x}": {"adv_name": f"Node{i}"} for i in range(95)}
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        assert await should_run_full_periodic_sync(mock_mc) is True

    @pytest.mark.asyncio
    async def test_should_skip_full_periodic_sync_below_trigger_threshold(self, test_db):
        """Occupancy below 95% of configured capacity does not trigger offload/reload."""
        from app.radio_sync import should_run_full_periodic_sync

        await AppSettingsRepository.update(max_radio_contacts=100)

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.NEW_CONTACT
        mock_result.payload = {f"{i:064x}": {"adv_name": f"Node{i}"} for i in range(94)}
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        assert await should_run_full_periodic_sync(mock_mc) is False

    @pytest.mark.asyncio
    async def test_disconnect_race_between_precheck_and_lock(self):
        """RadioDisconnectedError between is_connected and radio_operation()
        is caught by the outer except — loop survives and continues."""
        rm, _mc = _make_connected_manager()
        _disconnect_on_acquire(rm)
        mock_sleep, sleep_calls = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync", new_callable=AsyncMock
            ) as mock_check,
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_check.assert_not_called()
        mock_sync.assert_not_called()
        mock_time.assert_not_called()
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_skips_iteration(self):
        """RadioOperationBusyError is caught and sync functions are not called."""
        rm, _mc = _make_connected_manager()
        lock = await _pre_hold_lock(rm)
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        try:
            with (
                patch("app.radio_sync.radio_manager", rm),
                patch("asyncio.sleep", side_effect=mock_sleep),
                patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
                patch(
                    "app.radio_sync.should_run_full_periodic_sync", new_callable=AsyncMock
                ) as mock_check,
                patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
                patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
            ):
                await _periodic_sync_loop()
        finally:
            lock.release()

        mock_cleanup.assert_called_once()
        mock_check.assert_not_called()
        mock_sync.assert_not_called()
        mock_time.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_lock_scoped_mc_not_stale_global(self):
        """The mc yielded by radio_operation() is forwarded to
        sync_and_offload_all and sync_radio_time — not a stale
        radio_manager.meshcore read."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_sync.assert_called_once_with(mock_mc)
        mock_time.assert_called_once_with(mock_mc, warn_on_failure=False)

    @pytest.mark.asyncio
    async def test_skips_full_sync_below_threshold_but_still_syncs_time(self):
        """Periodic maintenance still does time sync when occupancy is below the trigger."""
        rm, mock_mc = _make_connected_manager()
        mock_sleep, _ = _sleep_controller(cancel_after=2)

        with (
            patch("app.radio_sync.radio_manager", rm),
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("app.radio_sync.cleanup_expired_acks") as mock_cleanup,
            patch(
                "app.radio_sync.should_run_full_periodic_sync",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock) as mock_sync,
            patch("app.radio_sync.sync_radio_time", new_callable=AsyncMock) as mock_time,
        ):
            await _periodic_sync_loop()

        mock_cleanup.assert_called_once()
        mock_sync.assert_not_called()
        mock_time.assert_called_once_with(mock_mc, warn_on_failure=False)


# ---------------------------------------------------------------------------
# _collect_repeater_telemetry — LPP sensor collection
# ---------------------------------------------------------------------------


class TestCollectRepeaterTelemetryLpp:
    """Verify that _collect_repeater_telemetry fetches LPP sensors."""

    @pytest.mark.asyncio
    async def test_lpp_sensors_included_in_data(self):
        from app.radio_sync import _collect_repeater_telemetry

        mc = MagicMock()
        mc.commands.add_contact = AsyncMock()
        mc.commands.req_status_sync = AsyncMock(
            return_value={"bat": 4100, "noise_floor": -110, "nb_recv": 10, "nb_sent": 5}
        )
        mc.commands.req_telemetry_sync = AsyncMock(
            return_value=[
                {"channel": 1, "type": "temperature", "value": 23.5},
                {"channel": 2, "type": "humidity", "value": 45.0},
            ]
        )

        contact = MagicMock()
        contact.public_key = "aabbccddeeff11223344"
        contact.name = "TestRepeater"
        contact.to_radio_dict.return_value = {}

        recorded_data = {}

        async def mock_record(public_key, timestamp, data):
            recorded_data.update(data)

        mock_fanout = MagicMock()
        mock_fanout.broadcast_telemetry = AsyncMock()

        with (
            patch(
                "app.radio_sync.RepeaterTelemetryRepository.record",
                new_callable=AsyncMock,
                side_effect=mock_record,
            ),
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            result = await _collect_repeater_telemetry(mc, contact)

        assert result is True
        assert "lpp_sensors" in recorded_data
        assert len(recorded_data["lpp_sensors"]) == 2
        assert recorded_data["lpp_sensors"][0]["type_name"] == "temperature"
        assert recorded_data["lpp_sensors"][0]["value"] == 23.5
        assert recorded_data["lpp_sensors"][1]["type_name"] == "humidity"

    @pytest.mark.asyncio
    async def test_lpp_failure_does_not_fail_collection(self):
        from app.radio_sync import _collect_repeater_telemetry

        mc = MagicMock()
        mc.commands.add_contact = AsyncMock()
        mc.commands.req_status_sync = AsyncMock(return_value={"bat": 4100, "noise_floor": -110})
        mc.commands.req_telemetry_sync = AsyncMock(side_effect=Exception("no sensors"))

        contact = MagicMock()
        contact.public_key = "aabbccddeeff11223344"
        contact.name = "TestRepeater"
        contact.to_radio_dict.return_value = {}

        recorded_data = {}

        async def mock_record(public_key, timestamp, data):
            recorded_data.update(data)

        mock_fanout = MagicMock()
        mock_fanout.broadcast_telemetry = AsyncMock()

        with (
            patch(
                "app.radio_sync.RepeaterTelemetryRepository.record",
                new_callable=AsyncMock,
                side_effect=mock_record,
            ),
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            result = await _collect_repeater_telemetry(mc, contact)

        assert result is True
        assert "lpp_sensors" not in recorded_data
        # Status data still present
        assert recorded_data["battery_volts"] == 4.1

    @pytest.mark.asyncio
    async def test_lpp_multivalue_sensors_skipped(self):
        from app.radio_sync import _collect_repeater_telemetry

        mc = MagicMock()
        mc.commands.add_contact = AsyncMock()
        mc.commands.req_status_sync = AsyncMock(return_value={"bat": 4000})
        mc.commands.req_telemetry_sync = AsyncMock(
            return_value=[
                {"channel": 1, "type": "temperature", "value": 23.5},
                {"channel": 3, "type": "gps", "value": {"lat": 1.0, "lon": 2.0, "alt": 3.0}},
            ]
        )

        contact = MagicMock()
        contact.public_key = "aabbccddeeff11223344"
        contact.name = "TestRepeater"
        contact.to_radio_dict.return_value = {}

        recorded_data = {}

        async def mock_record(public_key, timestamp, data):
            recorded_data.update(data)

        mock_fanout = MagicMock()
        mock_fanout.broadcast_telemetry = AsyncMock()

        with (
            patch(
                "app.radio_sync.RepeaterTelemetryRepository.record",
                new_callable=AsyncMock,
                side_effect=mock_record,
            ),
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            result = await _collect_repeater_telemetry(mc, contact)

        assert result is True
        assert len(recorded_data["lpp_sensors"]) == 1
        assert recorded_data["lpp_sensors"][0]["type_name"] == "temperature"

    @pytest.mark.asyncio
    async def test_lpp_none_response_no_sensors_key(self):
        from app.radio_sync import _collect_repeater_telemetry

        mc = MagicMock()
        mc.commands.add_contact = AsyncMock()
        mc.commands.req_status_sync = AsyncMock(return_value={"bat": 4000})
        mc.commands.req_telemetry_sync = AsyncMock(return_value=None)

        contact = MagicMock()
        contact.public_key = "aabbccddeeff11223344"
        contact.name = "TestRepeater"
        contact.to_radio_dict.return_value = {}

        recorded_data = {}

        async def mock_record(public_key, timestamp, data):
            recorded_data.update(data)

        mock_fanout = MagicMock()
        mock_fanout.broadcast_telemetry = AsyncMock()

        with (
            patch(
                "app.radio_sync.RepeaterTelemetryRepository.record",
                new_callable=AsyncMock,
                side_effect=mock_record,
            ),
            patch("app.fanout.manager.fanout_manager", mock_fanout),
        ):
            await _collect_repeater_telemetry(mc, contact)

        assert "lpp_sensors" not in recorded_data


class TestRunTelemetryCycleRoutedOnly:
    """Verify that _run_telemetry_cycle(routed_only=True) skips flood repeaters."""

    @pytest.mark.asyncio
    async def test_routed_only_skips_flood_contacts(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.models import AppSettings, Contact
        from app.radio_sync import _run_telemetry_cycle

        flood_key = "aa" * 32
        direct_key = "bb" * 32
        override_key = "cc" * 32

        flood_contact = Contact(
            public_key=flood_key,
            name="Flood",
            type=2,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
        )
        direct_contact = Contact(
            public_key=direct_key,
            name="Direct",
            type=2,
            direct_path="aabb",
            direct_path_len=1,
            direct_path_hash_mode=1,
        )
        override_contact = Contact(
            public_key=override_key,
            name="Override",
            type=2,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            route_override_path="ccdd",
            route_override_len=1,
            route_override_hash_mode=1,
        )

        settings = AppSettings(
            tracked_telemetry_repeaters=[flood_key, direct_key, override_key],
        )

        contact_map = {
            flood_key: flood_contact,
            direct_key: direct_contact,
            override_key: override_contact,
        }
        collected_keys: list[str] = []

        async def fake_get_by_key(key):
            return contact_map.get(key)

        async def fake_collect(mc, contact):
            collected_keys.append(contact.public_key)
            return True

        fake_radio_manager = MagicMock()
        fake_radio_manager.is_connected = True
        fake_radio_manager.radio_operation = MagicMock()

        # Make radio_operation an async context manager that yields a MagicMock
        fake_mc = MagicMock()

        class FakeRadioOp:
            async def __aenter__(self):
                return fake_mc

            async def __aexit__(self, *args):
                pass

        fake_radio_manager.radio_operation.return_value = FakeRadioOp()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch(
                "app.radio_sync.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=fake_get_by_key,
            ),
            patch("app.radio_sync._collect_repeater_telemetry", new=fake_collect),
            patch("app.radio_sync.radio_manager", fake_radio_manager),
        ):
            await _run_telemetry_cycle(routed_only=True)

        # Flood contact should be skipped; direct and override should be collected
        assert flood_key not in collected_keys
        assert direct_key in collected_keys
        assert override_key in collected_keys

    @pytest.mark.asyncio
    async def test_routed_only_skips_forced_flood_override(self):
        """A contact with a forced-flood override (path_len=-1) should be
        treated as flood even though effective_route_source is 'override'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.models import AppSettings, Contact
        from app.radio_sync import _run_telemetry_cycle

        forced_flood_key = "aa" * 32
        direct_key = "bb" * 32

        forced_flood_contact = Contact(
            public_key=forced_flood_key,
            name="ForcedFlood",
            type=2,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
            route_override_path="",
            route_override_len=-1,
            route_override_hash_mode=-1,
        )
        direct_contact = Contact(
            public_key=direct_key,
            name="Direct",
            type=2,
            direct_path="aabb",
            direct_path_len=1,
            direct_path_hash_mode=1,
        )

        # Verify the forced-flood contact reports "override" source
        assert forced_flood_contact.effective_route_source == "override"

        settings = AppSettings(
            tracked_telemetry_repeaters=[forced_flood_key, direct_key],
        )

        contact_map = {forced_flood_key: forced_flood_contact, direct_key: direct_contact}
        collected_keys: list[str] = []

        async def fake_get_by_key(key):
            return contact_map.get(key)

        async def fake_collect(mc, contact):
            collected_keys.append(contact.public_key)
            return True

        fake_radio_manager = MagicMock()
        fake_radio_manager.is_connected = True

        fake_mc = MagicMock()

        class FakeRadioOp:
            async def __aenter__(self):
                return fake_mc

            async def __aexit__(self, *args):
                pass

        fake_radio_manager.radio_operation.return_value = FakeRadioOp()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch(
                "app.radio_sync.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=fake_get_by_key,
            ),
            patch("app.radio_sync._collect_repeater_telemetry", new=fake_collect),
            patch("app.radio_sync.radio_manager", fake_radio_manager),
        ):
            await _run_telemetry_cycle(routed_only=True)

        # Forced-flood override should be excluded; direct should be collected
        assert forced_flood_key not in collected_keys
        assert direct_key in collected_keys

    @pytest.mark.asyncio
    async def test_full_cycle_includes_all_contacts(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.models import AppSettings, Contact
        from app.radio_sync import _run_telemetry_cycle

        flood_key = "aa" * 32
        direct_key = "bb" * 32

        flood_contact = Contact(
            public_key=flood_key,
            name="Flood",
            type=2,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
        )
        direct_contact = Contact(
            public_key=direct_key,
            name="Direct",
            type=2,
            direct_path="aabb",
            direct_path_len=1,
            direct_path_hash_mode=1,
        )

        settings = AppSettings(
            tracked_telemetry_repeaters=[flood_key, direct_key],
        )

        contact_map = {flood_key: flood_contact, direct_key: direct_contact}
        collected_keys: list[str] = []

        async def fake_get_by_key(key):
            return contact_map.get(key)

        async def fake_collect(mc, contact):
            collected_keys.append(contact.public_key)
            return True

        fake_radio_manager = MagicMock()
        fake_radio_manager.is_connected = True

        fake_mc = MagicMock()

        class FakeRadioOp:
            async def __aenter__(self):
                return fake_mc

            async def __aexit__(self, *args):
                pass

        fake_radio_manager.radio_operation.return_value = FakeRadioOp()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch(
                "app.radio_sync.ContactRepository.get_by_key",
                new_callable=AsyncMock,
                side_effect=fake_get_by_key,
            ),
            patch("app.radio_sync._collect_repeater_telemetry", new=fake_collect),
            patch("app.radio_sync.radio_manager", fake_radio_manager),
        ):
            await _run_telemetry_cycle(routed_only=False)

        # Full cycle collects both
        assert flood_key in collected_keys
        assert direct_key in collected_keys


# ---------------------------------------------------------------------------
# _telemetry_collect_loop — UTC modulo scheduler
# ---------------------------------------------------------------------------


class TestTelemetryCollectSchedulerDecision:
    """Verify the scheduler's run/skip decision at an hourly wake.

    We test the decision logic by stubbing the sleep + datetime functions
    and asserting ``_run_telemetry_cycle`` is called exactly on matching
    hours. Full end-to-end of the loop is covered implicitly by the
    existing telemetry-collect tests; what we're pinning here is the
    hour-modulo gate the new scheduler depends on.
    """

    @pytest.mark.asyncio
    async def test_skips_when_hour_modulo_mismatch(self):
        """At 09:00 UTC with interval 8h, the loop must NOT run a cycle."""
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=8,
        )
        ran = False

        async def fake_cycle(**_kwargs):
            nonlocal ran
            ran = True

        def make_fake_datetime(hour: int):
            class FakeDatetime:
                @classmethod
                def now(cls, tz=None):
                    import datetime as real_datetime

                    return real_datetime.datetime(2026, 4, 16, hour, 0, 0, tzinfo=real_datetime.UTC)

            return FakeDatetime

        sleep_count = 0

        async def fake_sleep(_duration):
            # The loop does: (1) initial-delay sleep, (2) sleep-to-top-of-hour,
            # then evaluates the run/skip decision. Allow both sleeps to
            # pass, then cancel on the 3rd (next iteration's top-of-hour sleep).
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
            patch("app.radio_sync.asyncio.sleep", new=fake_sleep),
            patch("app.radio_sync.datetime", new=make_fake_datetime(9)),
        ):
            try:
                await radio_sync._telemetry_collect_loop()
            except asyncio.CancelledError:
                pass

        assert ran is False, "09:00 UTC is not a multiple of 8h; cycle must not run"

    @pytest.mark.asyncio
    async def test_runs_when_hour_modulo_matches(self):
        """At 16:00 UTC with interval 8h, the loop must run a cycle."""
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=8,
        )
        ran = False

        async def fake_cycle(**_kwargs):
            nonlocal ran
            ran = True

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                import datetime as real_datetime

                return real_datetime.datetime(2026, 4, 16, 16, 0, 0, tzinfo=real_datetime.UTC)

        sleep_count = 0

        async def fake_sleep(_duration):
            # Let the loop's initial-delay + top-of-hour sleeps pass; cancel
            # on the third sleep (next iteration's top-of-hour wake).
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
            patch("app.radio_sync.asyncio.sleep", new=fake_sleep),
            patch("app.radio_sync.datetime", new=FakeDatetime),
        ):
            try:
                await radio_sync._telemetry_collect_loop()
            except asyncio.CancelledError:
                pass

        assert ran is True, "16:00 UTC is a multiple of 8h; cycle must run"

    @pytest.mark.asyncio
    async def test_skips_when_no_repeaters_tracked(self):
        """Empty tracked list short-circuits regardless of modulo match."""
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(tracked_telemetry_repeaters=[], telemetry_interval_hours=8)
        ran = False

        async def fake_cycle(**_kwargs):
            nonlocal ran
            ran = True

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                import datetime as real_datetime

                return real_datetime.datetime(2026, 4, 16, 16, 0, 0, tzinfo=real_datetime.UTC)

        sleep_count = 0

        async def fake_sleep(_duration):
            # Let the loop's initial-delay + top-of-hour sleeps pass; cancel
            # on the third sleep (next iteration's top-of-hour wake).
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
            patch("app.radio_sync.asyncio.sleep", new=fake_sleep),
            patch("app.radio_sync.datetime", new=FakeDatetime),
        ):
            try:
                await radio_sync._telemetry_collect_loop()
            except asyncio.CancelledError:
                pass

        assert ran is False, "No tracked repeaters: no cycle regardless of hour"

    @pytest.mark.asyncio
    async def test_runs_on_boundary_immediately_after_initial_delay(self):
        """Regression test: if the post-boot initial delay finishes inside a
        matching hour, the cycle must run even if the first
        sleep-to-next-top-of-hour would otherwise carry us past the boundary.

        Scenario: server starts at 23:59:30 UTC with a 24-hour interval. The
        60-second boot guard pushes the first check into 00:00:30 — a matching
        hour that we must NOT skip. Before the fix, the loop went straight to
        sleeping until 01:00 and then failing the modulo, missing the entire
        day's only scheduled collection.
        """
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=24,  # daily cadence; only matching hour is 00
        )
        ran = False

        async def fake_cycle(**_kwargs):
            nonlocal ran
            ran = True

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                import datetime as real_datetime

                # Simulates "initial delay just ended at 00:00:30 UTC on a
                # restart that began at 23:59:30." Without the post-boot
                # boundary check, the loop would have skipped this.
                return real_datetime.datetime(2026, 4, 16, 0, 0, 30, tzinfo=real_datetime.UTC)

        sleep_count = 0

        async def fake_sleep(_duration):
            # Let the initial delay pass, then cancel before the first
            # top-of-hour sleep so we isolate the post-boot check as the
            # only opportunity to run.
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
            patch("app.radio_sync.asyncio.sleep", new=fake_sleep),
            patch("app.radio_sync.datetime", new=FakeDatetime),
        ):
            try:
                await radio_sync._telemetry_collect_loop()
            except asyncio.CancelledError:
                pass

        assert ran is True, (
            "Post-boot check must fire the due 00:00 cycle; otherwise a "
            "restart near midnight suppresses the whole day's collection."
        )

    @pytest.mark.asyncio
    async def test_clamps_up_when_preferred_illegal_for_current_count(self):
        """5 tracked repeaters with saved pref 1h: scheduler should use 6h.

        At 02:00 UTC: 2 % 6 == 2 (not a run), so cycle must not fire.
        If clamping were skipped, 2 % 1 == 0 and cycle would incorrectly run.
        """
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32] * 5,
            telemetry_interval_hours=1,  # illegal at N=5; shortest legal is 6h
        )
        ran = False

        async def fake_cycle(**_kwargs):
            nonlocal ran
            ran = True

        class FakeDatetime:
            @classmethod
            def now(cls, tz=None):
                import datetime as real_datetime

                return real_datetime.datetime(2026, 4, 16, 2, 0, 0, tzinfo=real_datetime.UTC)

        sleep_count = 0

        async def fake_sleep(_duration):
            # Let the loop's initial-delay + top-of-hour sleeps pass; cancel
            # on the third sleep (next iteration's top-of-hour wake).
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
            patch("app.radio_sync.asyncio.sleep", new=fake_sleep),
            patch("app.radio_sync.datetime", new=FakeDatetime),
        ):
            try:
                await radio_sync._telemetry_collect_loop()
            except asyncio.CancelledError:
                pass

        assert ran is False, (
            "Clamping to 6h must prevent the 02:00 run that 1h cadence would've triggered"
        )


class TestRoutedHourlySchedulerDecision:
    """Verify the routed_hourly feature in _maybe_run_scheduled_cycle."""

    @pytest.mark.asyncio
    async def test_routed_hourly_fires_on_non_modulo_hour(self):
        """At 09:00 UTC with 8h interval and routed_hourly=True, the scheduler
        should call _run_telemetry_cycle(routed_only=True)."""
        import datetime as real_datetime
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=8,
            telemetry_routed_hourly=True,
        )
        calls = []

        async def fake_cycle(*, routed_only=False, **_kwargs):
            calls.append({"routed_only": routed_only})

        now = real_datetime.datetime(2026, 4, 16, 9, 0, 0, tzinfo=real_datetime.UTC)

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
        ):
            await radio_sync._maybe_run_scheduled_cycle(now)

        assert len(calls) == 1
        assert calls[0]["routed_only"] is True

    @pytest.mark.asyncio
    async def test_routed_hourly_disabled_skips_non_modulo_hour(self):
        """At 09:00 UTC with 8h interval and routed_hourly=False, nothing runs."""
        import datetime as real_datetime
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=8,
            telemetry_routed_hourly=False,
        )
        calls = []

        async def fake_cycle(*, routed_only=False, **_kwargs):
            calls.append({"routed_only": routed_only})

        now = real_datetime.datetime(2026, 4, 16, 9, 0, 0, tzinfo=real_datetime.UTC)

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
        ):
            await radio_sync._maybe_run_scheduled_cycle(now)

        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_modulo_hour_runs_full_cycle_even_with_routed_hourly(self):
        """At 16:00 UTC with 8h interval, a normal full cycle runs regardless
        of whether routed_hourly is enabled — it covers all repeaters."""
        import datetime as real_datetime
        from unittest.mock import AsyncMock, patch

        from app import radio_sync
        from app.models import AppSettings

        settings = AppSettings(
            tracked_telemetry_repeaters=["aa" * 32],
            telemetry_interval_hours=8,
            telemetry_routed_hourly=True,
        )
        calls = []

        async def fake_cycle(*, routed_only=False, **_kwargs):
            calls.append({"routed_only": routed_only})

        now = real_datetime.datetime(2026, 4, 16, 16, 0, 0, tzinfo=real_datetime.UTC)

        with (
            patch(
                "app.radio_sync.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=settings,
            ),
            patch("app.radio_sync._run_telemetry_cycle", new=fake_cycle),
        ):
            await radio_sync._maybe_run_scheduled_cycle(now)

        assert len(calls) == 1
        assert calls[0]["routed_only"] is False


# ---------------------------------------------------------------------------
# get_contacts_selected_for_radio_sync — DM-active prioritization
# ---------------------------------------------------------------------------


class TestContactSelectionDmActive:
    """Verify that tier 2 prioritizes contacts with recent DM activity."""

    @pytest.mark.asyncio
    async def test_incoming_dm_contact_selected_over_advert_only(self, test_db):
        """A contact who sent us a DM should be prioritized over one who only advertised."""
        from app.radio_sync import get_contacts_selected_for_radio_sync

        # Create two non-repeater contacts
        dm_sender_key = "aa" * 32
        advert_only_key = "bb" * 32

        await test_db.conn.execute(
            "INSERT INTO contacts (public_key, name, type, last_seen, last_advert) VALUES (?, ?, 1, 100, 100)",
            (dm_sender_key, "DM Sender"),
        )
        await test_db.conn.execute(
            "INSERT INTO contacts (public_key, name, type, last_seen, last_advert) VALUES (?, ?, 1, 200, 200)",
            (advert_only_key, "Advert Only"),
        )

        # DM Sender sent us a message (incoming DM)
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES ('PRIV', ?, 'hello', 300)",
            (dm_sender_key,),
        )
        await test_db.conn.commit()

        with patch(
            "app.radio_sync.AppSettingsRepository.get",
            new_callable=AsyncMock,
            return_value=MagicMock(max_radio_contacts=200, tracked_telemetry_repeaters=[]),
        ):
            selected = await get_contacts_selected_for_radio_sync()

        keys = [c.public_key for c in selected]
        assert dm_sender_key in keys
        assert advert_only_key in keys
        # DM Sender should come before Advert Only (tier 2 before tier 3)
        assert keys.index(dm_sender_key) < keys.index(advert_only_key)

    @pytest.mark.asyncio
    async def test_outgoing_dm_contact_also_selected(self, test_db):
        """A contact we sent a DM to should also appear via DM-active tier."""
        from app.radio_sync import get_contacts_selected_for_radio_sync

        contact_key = "cc" * 32
        await test_db.conn.execute(
            "INSERT INTO contacts (public_key, name, type) VALUES (?, ?, 1)",
            (contact_key, "Outgoing Target"),
        )
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing) VALUES ('PRIV', ?, 'hey', 300, 1)",
            (contact_key,),
        )
        await test_db.conn.commit()

        with patch(
            "app.radio_sync.AppSettingsRepository.get",
            new_callable=AsyncMock,
            return_value=MagicMock(max_radio_contacts=200, tracked_telemetry_repeaters=[]),
        ):
            selected = await get_contacts_selected_for_radio_sync()

        keys = [c.public_key for c in selected]
        assert contact_key in keys

    @pytest.mark.asyncio
    async def test_repeaters_excluded_from_dm_active_tier(self, test_db):
        """Repeater contacts should not appear in tier 2 even with DM activity."""
        from app.radio_sync import get_contacts_selected_for_radio_sync

        repeater_key = "dd" * 32
        await test_db.conn.execute(
            "INSERT INTO contacts (public_key, name, type) VALUES (?, ?, 2)",
            (repeater_key, "Repeater"),
        )
        await test_db.conn.execute(
            "INSERT INTO messages (type, conversation_key, text, received_at) VALUES ('PRIV', ?, 'cmd', 300)",
            (repeater_key,),
        )
        await test_db.conn.commit()

        with patch(
            "app.radio_sync.AppSettingsRepository.get",
            new_callable=AsyncMock,
            return_value=MagicMock(max_radio_contacts=200, tracked_telemetry_repeaters=[]),
        ):
            selected = await get_contacts_selected_for_radio_sync()

        keys = [c.public_key for c in selected]
        assert repeater_key not in keys
