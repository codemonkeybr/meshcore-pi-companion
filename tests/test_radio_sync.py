"""Tests for radio_sync module.

These tests verify the polling pause mechanism, radio time sync,
contact/channel sync operations, and default channel management.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

from app.database import Database
from app.models import Favorite
from app.radio_sync import (
    is_polling_paused,
    pause_polling,
    sync_radio_time,
    sync_recent_contacts_to_radio,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)


@pytest.fixture
async def test_db():
    """Create an in-memory test database with schema + migrations."""
    import app.repository as repo_module

    db = Database(":memory:")
    await db.connect()

    original_db = repo_module.db
    repo_module.db = db

    try:
        yield db
    finally:
        repo_module.db = original_db
        await db.disconnect()


@pytest.fixture(autouse=True)
def reset_sync_state():
    """Reset polling pause state and sync timestamp before and after each test."""
    import app.radio_sync as radio_sync

    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0
    yield
    radio_sync._polling_pause_count = 0
    radio_sync._last_contact_sync = 0.0


KEY_A = "aa" * 32
KEY_B = "bb" * 32


async def _insert_contact(
    public_key=KEY_A,
    name="Alice",
    on_radio=False,
    contact_type=0,
    last_contacted=None,
    last_advert=None,
):
    """Insert a contact into the test database."""
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
            "flags": 0,
            "last_path": None,
            "last_path_len": -1,
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
    async def test_triple_nested_pause(self):
        """Three levels of nesting work correctly."""
        async with pause_polling():
            async with pause_polling():
                async with pause_polling():
                    assert is_polling_paused()
                assert is_polling_paused()
            assert is_polling_paused()
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

    @pytest.mark.asyncio
    async def test_counter_increments_and_decrements(self):
        """Counter correctly tracks pause depth."""
        import app.radio_sync as radio_sync

        assert radio_sync._polling_pause_count == 0

        async with pause_polling():
            assert radio_sync._polling_pause_count == 1

            async with pause_polling():
                assert radio_sync._polling_pause_count == 2

            assert radio_sync._polling_pause_count == 1

        assert radio_sync._polling_pause_count == 0


class TestSyncRadioTime:
    """Test the radio time sync function."""

    @pytest.mark.asyncio
    async def test_returns_false_when_not_connected(self):
        """sync_radio_time returns False when radio is not connected."""
        with patch("app.radio_sync.radio_manager") as mock_manager:
            mock_manager.meshcore = None
            result = await sync_radio_time()
            assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        """sync_radio_time returns True when time is set successfully."""
        mock_mc = MagicMock()
        mock_mc.commands.set_time = AsyncMock()

        with patch("app.radio_sync.radio_manager") as mock_manager:
            mock_manager.meshcore = mock_mc
            result = await sync_radio_time()

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

        with patch("app.radio_sync.radio_manager") as mock_manager:
            mock_manager.meshcore = mock_mc
            result = await sync_radio_time()

            assert result is False


class TestSyncRecentContactsToRadio:
    """Test the sync_recent_contacts_to_radio function."""

    @pytest.mark.asyncio
    async def test_loads_contacts_not_on_radio(self, test_db):
        """Contacts not on radio are added via add_contact."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        # Verify contacts are now marked as on_radio in DB
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice.on_radio is True
        assert bob.on_radio is True

    @pytest.mark.asyncio
    async def test_favorites_loaded_before_recent_contacts(self, test_db):
        """Favorite contacts are loaded first, then recents until limit."""
        await _insert_contact(KEY_A, "Alice", last_contacted=100)
        await _insert_contact(KEY_B, "Bob", last_contacted=2000)
        await _insert_contact("cc" * 32, "Carol", last_contacted=1000)

        # Set max_radio_contacts=2 and add KEY_A as favorite
        await AppSettingsRepository.update(
            max_radio_contacts=2,
            favorites=[Favorite(type="contact", id=KEY_A)],
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        # KEY_A (favorite) should be loaded first, then KEY_B (most recent)
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B]

    @pytest.mark.asyncio
    async def test_favorite_contact_not_loaded_twice_if_also_recent(self, test_db):
        """A favorite contact that is also recent is loaded only once."""
        await _insert_contact(KEY_A, "Alice", last_contacted=2000)
        await _insert_contact(KEY_B, "Bob", last_contacted=1000)

        await AppSettingsRepository.update(
            max_radio_contacts=2,
            favorites=[Favorite(type="contact", id=KEY_A)],
        )

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 2
        loaded_keys = [
            call.args[0]["public_key"] for call in mock_mc.commands.add_contact.call_args_list
        ]
        assert loaded_keys == [KEY_A, KEY_B]

    @pytest.mark.asyncio
    async def test_skips_contacts_already_on_radio(self, test_db):
        """Contacts already on radio are counted but not re-added."""
        await _insert_contact(KEY_A, "Alice", on_radio=True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # Found
        mock_mc.commands.add_contact = AsyncMock()

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["already_on_radio"] == 1
        mock_mc.commands.add_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttled_when_called_quickly(self, test_db):
        """Second call within throttle window returns throttled result."""
        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

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
    async def test_marks_on_radio_when_found_but_not_flagged(self, test_db):
        """Contact found on radio but not flagged gets set_on_radio(True)."""
        await _insert_contact(KEY_A, "Alice", on_radio=False)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # Found

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["already_on_radio"] == 1
        # Should update the flag since contact.on_radio was False
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.on_radio is True

    @pytest.mark.asyncio
    async def test_handles_add_failure(self, test_db):
        """Failed add_contact increments the failed counter."""
        await _insert_contact(KEY_A, "Alice")

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)
        mock_result = MagicMock()
        mock_result.type = EventType.ERROR
        mock_result.payload = {"error": "Radio full"}
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_recent_contacts_to_radio()

        assert result["loaded"] == 0
        assert result["failed"] == 1


class TestSyncAndOffloadContacts:
    """Test sync_and_offload_contacts: pull contacts from radio, save to DB, remove from radio."""

    @pytest.mark.asyncio
    async def test_returns_error_when_not_connected(self):
        """Returns error dict when radio is not connected."""
        from app.radio_sync import sync_and_offload_contacts

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            result = await sync_and_offload_contacts()

        assert result["synced"] == 0
        assert result["removed"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_syncs_and_removes_contacts(self, test_db):
        """Contacts are upserted to DB and removed from radio."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT  # Not ERROR
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_contacts()

        assert result["synced"] == 2
        assert result["removed"] == 2

        # Verify contacts are in real DB
        alice = await ContactRepository.get_by_key(KEY_A)
        bob = await ContactRepository.get_by_key(KEY_B)
        assert alice is not None
        assert alice.name == "Alice"
        assert bob is not None
        assert bob.name == "Bob"

    @pytest.mark.asyncio
    async def test_claims_prefix_messages_for_each_contact(self, test_db):
        """claim_prefix_messages is called for each synced contact."""
        from app.radio_sync import sync_and_offload_contacts

        # Pre-insert a message with a prefix key that matches KEY_A
        await MessageRepository.create(
            msg_type="PRIV",
            text="Hello from prefix",
            received_at=1700000000,
            conversation_key=KEY_A[:12],
            sender_timestamp=1700000000,
        )

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            await sync_and_offload_contacts()

        # Verify the prefix message was claimed (promoted to full key)
        messages = await MessageRepository.get_all(conversation_key=KEY_A)
        assert len(messages) == 1
        assert messages[0].conversation_key == KEY_A.lower()

    @pytest.mark.asyncio
    async def test_handles_remove_failure_gracefully(self, test_db):
        """Failed remove_contact logs warning but continues to next contact."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_fail_result = MagicMock()
        mock_fail_result.type = EventType.ERROR
        mock_fail_result.payload = {"error": "busy"}

        mock_ok_result = MagicMock()
        mock_ok_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        # First remove fails, second succeeds
        mock_mc.commands.remove_contact = AsyncMock(side_effect=[mock_fail_result, mock_ok_result])

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_contacts()

        # Both contacts synced, but only one removed successfully
        assert result["synced"] == 2
        assert result["removed"] == 1

    @pytest.mark.asyncio
    async def test_handles_remove_exception_gracefully(self, test_db):
        """Exception during remove_contact is caught and processing continues."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(side_effect=Exception("Timeout"))

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_contacts()

        assert result["synced"] == 1
        assert result["removed"] == 0

    @pytest.mark.asyncio
    async def test_returns_error_when_get_contacts_fails(self):
        """Error result from get_contacts returns error dict."""
        from app.radio_sync import sync_and_offload_contacts

        mock_error_result = MagicMock()
        mock_error_result.type = EventType.ERROR
        mock_error_result.payload = {"error": "radio busy"}

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_error_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_contacts()

        assert result["synced"] == 0
        assert result["removed"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_upserts_with_on_radio_false(self, test_db):
        """Contacts are upserted with on_radio=False (being removed from radio)."""
        from app.radio_sync import sync_and_offload_contacts

        contact_payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}

        mock_get_result = MagicMock()
        mock_get_result.type = EventType.NEW_CONTACT
        mock_get_result.payload = contact_payload

        mock_remove_result = MagicMock()
        mock_remove_result.type = EventType.OK

        mock_mc = MagicMock()
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_get_result)
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_remove_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            await sync_and_offload_contacts()

        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.on_radio is False


class TestSyncAndOffloadChannels:
    """Test sync_and_offload_channels: pull channels from radio, save to DB, clear from radio."""

    @pytest.mark.asyncio
    async def test_returns_error_when_not_connected(self):
        """Returns error dict when radio is not connected."""
        from app.radio_sync import sync_and_offload_channels

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            result = await sync_and_offload_channels()

        assert result["synced"] == 0
        assert result["cleared"] == 0
        assert "error" in result

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_channels()

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_channels()

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_channels()

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            await sync_and_offload_channels()

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            await sync_and_offload_channels()

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

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_channels()

        assert result["synced"] == 2
        assert result["cleared"] == 1

    @pytest.mark.asyncio
    async def test_iterates_all_40_channel_slots(self):
        """All 40 channel slots are checked."""
        from app.radio_sync import sync_and_offload_channels

        empty_result = MagicMock()
        empty_result.type = EventType.ERROR

        mock_mc = MagicMock()
        mock_mc.commands.get_channel = AsyncMock(return_value=empty_result)

        with patch("app.radio_sync.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc

            result = await sync_and_offload_channels()

        assert mock_mc.commands.get_channel.call_count == 40
        assert result["synced"] == 0
        assert result["cleared"] == 0


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
