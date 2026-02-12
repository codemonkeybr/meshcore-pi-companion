"""Tests for event handler logic.

These tests verify the ACK tracking mechanism for direct message
delivery confirmation, contact message handling, and event registration.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import Database
from app.event_handlers import (
    _active_subscriptions,
    _cleanup_expired_acks,
    _pending_acks,
    register_event_handlers,
    track_pending_ack,
)
from app.repository import (
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
def clear_test_state():
    """Clear pending ACKs and subscriptions before each test."""
    _pending_acks.clear()
    _active_subscriptions.clear()
    yield
    _pending_acks.clear()
    _active_subscriptions.clear()


class TestAckTracking:
    """Test ACK tracking for direct messages."""

    def test_track_pending_ack_stores_correctly(self):
        """Pending ACKs are stored with message ID and timeout."""
        track_pending_ack("abc123", message_id=42, timeout_ms=5000)

        assert "abc123" in _pending_acks
        msg_id, created_at, timeout = _pending_acks["abc123"]
        assert msg_id == 42
        assert timeout == 5000
        assert created_at <= time.time()

    def test_multiple_acks_tracked_independently(self):
        """Multiple pending ACKs can be tracked simultaneously."""
        track_pending_ack("ack1", message_id=1, timeout_ms=1000)
        track_pending_ack("ack2", message_id=2, timeout_ms=2000)
        track_pending_ack("ack3", message_id=3, timeout_ms=3000)

        assert len(_pending_acks) == 3
        assert _pending_acks["ack1"][0] == 1
        assert _pending_acks["ack2"][0] == 2
        assert _pending_acks["ack3"][0] == 3

    def test_cleanup_removes_expired_acks(self):
        """Expired ACKs are removed during cleanup."""
        # Add an ACK that's "expired" (created in the past with short timeout)
        _pending_acks["expired"] = (1, time.time() - 100, 1000)  # Created 100s ago, 1s timeout
        _pending_acks["valid"] = (2, time.time(), 60000)  # Created now, 60s timeout

        _cleanup_expired_acks()

        assert "expired" not in _pending_acks
        assert "valid" in _pending_acks

    def test_cleanup_uses_2x_timeout_buffer(self):
        """Cleanup uses 2x timeout as buffer before expiring."""
        # ACK created 5 seconds ago with 10 second timeout
        # 2x buffer = 20 seconds, so should NOT be expired yet
        _pending_acks["recent"] = (1, time.time() - 5, 10000)

        _cleanup_expired_acks()

        assert "recent" in _pending_acks


class TestAckEventHandler:
    """Test the on_ack event handler."""

    @pytest.mark.asyncio
    async def test_ack_matches_pending_message(self, test_db):
        """Matching ACK code updates message and broadcasts."""
        from app.event_handlers import on_ack

        # Insert a real message to get a valid ID
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Hello",
            received_at=1700000000,
            conversation_key="aa" * 32,
            sender_timestamp=1700000000,
        )

        # Setup pending ACK with the real message ID
        track_pending_ack("deadbeef", message_id=msg_id, timeout_ms=10000)

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "deadbeef"}

            await on_ack(MockEvent())

            # Verify ack count incremented (real DB)
            ack_count = await MessageRepository.get_ack_count(msg_id)
            assert ack_count == 1

            # Verify broadcast sent with ack_count
            mock_broadcast.assert_called_once_with(
                "message_acked", {"message_id": msg_id, "ack_count": 1}
            )

            # Verify pending ACK removed
            assert "deadbeef" not in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_no_match_does_nothing(self, test_db):
        """Non-matching ACK code is ignored."""
        from app.event_handlers import on_ack

        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Hello",
            received_at=1700000000,
            conversation_key="aa" * 32,
            sender_timestamp=1700000000,
        )
        track_pending_ack("expected", message_id=msg_id, timeout_ms=10000)

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": "different"}

            await on_ack(MockEvent())

            # Ack count should remain 0
            ack_count = await MessageRepository.get_ack_count(msg_id)
            assert ack_count == 0

            mock_broadcast.assert_not_called()
            assert "expected" in _pending_acks

    @pytest.mark.asyncio
    async def test_ack_empty_code_ignored(self, test_db):
        """ACK with empty code is ignored."""
        from app.event_handlers import on_ack

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"code": ""}

            await on_ack(MockEvent())

            mock_broadcast.assert_not_called()


class TestContactMessageCLIFiltering:
    """Test that CLI responses (txt_type=1) are filtered out."""

    @pytest.mark.asyncio
    async def test_cli_response_skipped_not_stored(self, test_db):
        """CLI responses (txt_type=1) are not stored in database."""
        from app.event_handlers import on_contact_message

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "clock: 2024-01-01 12:00:00",
                    "txt_type": 1,  # CLI response
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Should NOT broadcast via WebSocket
            mock_broadcast.assert_not_called()

            # Should NOT have stored anything in DB
            messages = await MessageRepository.get_all()
            assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_normal_message_schedules_bot_in_background(self, test_db):
        """Normal messages should schedule bot execution without blocking."""
        from app.event_handlers import on_contact_message

        def _capture_task(coro):
            coro.close()
            return MagicMock()

        with (
            patch("app.event_handlers.broadcast_event"),
            patch("app.event_handlers.asyncio.create_task", side_effect=_capture_task) as mock_task,
            patch("app.bot.run_bot_for_message", new_callable=AsyncMock) as mock_bot,
        ):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Hello, bot",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            mock_task.assert_called_once()
            mock_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_normal_message_still_processed(self, test_db):
        """Normal messages (txt_type=0) are still processed normally."""
        from app.event_handlers import on_contact_message

        with (
            patch("app.event_handlers.broadcast_event") as mock_broadcast,
            patch("app.bot.run_bot_for_message", new_callable=AsyncMock),
        ):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Hello, this is a normal message",
                    "txt_type": 0,  # Normal message (default)
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # SHOULD be stored in database
            messages = await MessageRepository.get_all()
            assert len(messages) == 1
            assert messages[0].text == "Hello, this is a normal message"

            # SHOULD broadcast via WebSocket
            mock_broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_payload_has_correct_acked_type(self, test_db):
        """Broadcast payload should have acked as integer 0, not boolean False."""
        from app.event_handlers import on_contact_message

        with (
            patch("app.event_handlers.broadcast_event") as mock_broadcast,
            patch("app.bot.run_bot_for_message", new_callable=AsyncMock),
        ):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Test message",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Verify broadcast was called
            mock_broadcast.assert_called_once()
            call_args = mock_broadcast.call_args

            # First arg is event type, second is payload dict
            event_type, payload = call_args[0]
            assert event_type == "message"
            assert payload["acked"] == 0
            assert payload["acked"] is not False  # Ensure it's int, not bool
            assert isinstance(payload["acked"], int)

    @pytest.mark.asyncio
    async def test_missing_txt_type_defaults_to_normal(self, test_db):
        """Messages without txt_type field are treated as normal (not filtered)."""
        from app.event_handlers import on_contact_message

        with (
            patch("app.event_handlers.broadcast_event"),
            patch("app.bot.run_bot_for_message", new_callable=AsyncMock),
        ):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123def456",
                    "text": "Message without txt_type field",
                    "sender_timestamp": 1700000000,
                    # No txt_type field
                }

            await on_contact_message(MockEvent())

            # SHOULD still be processed (defaults to txt_type=0)
            messages = await MessageRepository.get_all()
            assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_ambiguous_prefix_stores_dm_under_prefix(self, test_db):
        """Ambiguous sender prefixes should still be stored under the prefix key."""
        from app.event_handlers import on_contact_message

        # Insert two contacts that share the same prefix to trigger ambiguity
        await ContactRepository.upsert(
            {
                "public_key": "abc123" + "00" * 29,
                "name": "ContactA",
                "type": 1,
                "flags": 0,
            }
        )
        await ContactRepository.upsert(
            {
                "public_key": "abc123" + "ff" * 29,
                "name": "ContactB",
                "type": 1,
                "flags": 0,
            }
        )

        with (
            patch("app.event_handlers.broadcast_event") as mock_broadcast,
            patch("app.bot.run_bot_for_message", new_callable=AsyncMock),
        ):

            class MockEvent:
                payload = {
                    "pubkey_prefix": "abc123",
                    "text": "hello from ambiguous prefix",
                    "txt_type": 0,
                    "sender_timestamp": 1700000000,
                }

            await on_contact_message(MockEvent())

            # Should store in DB under the prefix key
            messages = await MessageRepository.get_all()
            assert len(messages) == 1
            assert messages[0].conversation_key == "abc123"

            mock_broadcast.assert_called_once()
            _, payload = mock_broadcast.call_args.args
            assert payload["conversation_key"] == "abc123"


class TestEventHandlerRegistration:
    """Test event handler registration and cleanup."""

    def test_register_handlers_tracks_subscriptions(self):
        """Registering handlers populates _active_subscriptions."""
        mock_meshcore = MagicMock()
        mock_subscription = MagicMock()
        mock_meshcore.subscribe.return_value = mock_subscription

        register_event_handlers(mock_meshcore)

        # Should have 5 subscriptions (one per event type)
        assert len(_active_subscriptions) == 5
        assert mock_meshcore.subscribe.call_count == 5

    def test_register_handlers_twice_does_not_duplicate(self):
        """Calling register_event_handlers twice unsubscribes old handlers first."""
        mock_meshcore = MagicMock()

        # First call: create mock subscriptions
        first_subs = [MagicMock() for _ in range(5)]
        mock_meshcore.subscribe.side_effect = first_subs
        register_event_handlers(mock_meshcore)

        assert len(_active_subscriptions) == 5
        first_sub_objects = list(_active_subscriptions)

        # Second call: create new mock subscriptions
        second_subs = [MagicMock() for _ in range(5)]
        mock_meshcore.subscribe.side_effect = second_subs
        register_event_handlers(mock_meshcore)

        # Old subscriptions should have been unsubscribed
        for sub in first_sub_objects:
            sub.unsubscribe.assert_called_once()

        # Should still have exactly 5 subscriptions (not 10)
        assert len(_active_subscriptions) == 5

        # New subscriptions should be the second batch
        for sub in second_subs:
            assert sub in _active_subscriptions

    def test_register_handlers_clears_before_adding(self):
        """The subscription list is cleared before adding new subscriptions."""
        mock_meshcore = MagicMock()
        mock_meshcore.subscribe.return_value = MagicMock()

        # Pre-populate with stale subscriptions (simulating a bug scenario)
        stale_sub = MagicMock()
        _active_subscriptions.append(stale_sub)
        _active_subscriptions.append(stale_sub)

        register_event_handlers(mock_meshcore)

        # Stale subscriptions should have been unsubscribed
        assert stale_sub.unsubscribe.call_count == 2

        # Should have exactly 5 fresh subscriptions
        assert len(_active_subscriptions) == 5

    def test_register_handlers_survives_unsubscribe_exception(self):
        """If unsubscribe() throws, registration still completes successfully."""
        mock_meshcore = MagicMock()
        mock_meshcore.subscribe.return_value = MagicMock()

        # Create subscriptions where unsubscribe raises an exception
        bad_sub = MagicMock()
        bad_sub.unsubscribe.side_effect = RuntimeError("Dispatcher is dead")
        _active_subscriptions.append(bad_sub)

        good_sub = MagicMock()
        _active_subscriptions.append(good_sub)

        # Should not raise despite the exception
        register_event_handlers(mock_meshcore)

        # Both unsubscribe methods should have been called
        bad_sub.unsubscribe.assert_called_once()
        good_sub.unsubscribe.assert_called_once()

        # Should have exactly 5 fresh subscriptions
        assert len(_active_subscriptions) == 5


class TestOnPathUpdate:
    """Test the on_path_update event handler."""

    @pytest.mark.asyncio
    async def test_updates_path_for_existing_contact(self, test_db):
        """Path is updated when the contact exists in the database."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "aa" * 32,
                "name": "Alice",
                "type": 1,
                "flags": 0,
            }
        )

        class MockEvent:
            payload = {
                "pubkey_prefix": "aaaaaa",
                "path": "0102",
                "path_len": 2,
            }

        await on_path_update(MockEvent())

        # Verify path was updated in DB
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_path == "0102"
        assert contact.last_path_len == 2

    @pytest.mark.asyncio
    async def test_does_nothing_when_contact_not_found(self, test_db):
        """No update is attempted when the contact is not in the database."""
        from app.event_handlers import on_path_update

        class MockEvent:
            payload = {
                "pubkey_prefix": "unknown",
                "path": "0102",
                "path_len": 2,
            }

        # Should not raise
        await on_path_update(MockEvent())

    @pytest.mark.asyncio
    async def test_uses_defaults_for_missing_payload_fields(self, test_db):
        """Missing payload fields fall back to defaults (empty path, -1 length)."""
        from app.event_handlers import on_path_update

        await ContactRepository.upsert(
            {
                "public_key": "bb" * 32,
                "name": "Bob",
                "type": 1,
                "flags": 0,
            }
        )

        class MockEvent:
            payload = {}

        await on_path_update(MockEvent())

        # With empty prefix, get_by_key_prefix("") should return None since
        # no key starts with "" uniquely (if multiple contacts exist) or
        # the single contact if only one. But with prefix="", the LIKE query
        # matches all contacts. With exactly one contact, it returns it.
        # The update_path call sets path="" and path_len=-1.
        contact = await ContactRepository.get_by_key("bb" * 32)
        assert contact is not None
        assert contact.last_path == ""
        assert contact.last_path_len == -1


class TestOnNewContact:
    """Test the on_new_contact event handler."""

    @pytest.mark.asyncio
    async def test_creates_contact_and_broadcasts(self, test_db):
        """Valid new contact is upserted and broadcast via WebSocket."""
        from app.event_handlers import on_new_contact

        with (
            patch("app.event_handlers.broadcast_event") as mock_broadcast,
            patch("app.event_handlers.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000

            class MockEvent:
                payload = {
                    "public_key": "cc" * 32,
                    "adv_name": "Charlie",
                    "type": 1,
                    "flags": 0,
                }

            await on_new_contact(MockEvent())

            # Verify contact was created in real DB
            contact = await ContactRepository.get_by_key("cc" * 32)
            assert contact is not None
            assert contact.name == "Charlie"
            assert contact.on_radio is True
            assert contact.last_seen == 1700000000

            mock_broadcast.assert_called_once()
            event_type, contact_data = mock_broadcast.call_args[0]
            assert event_type == "contact"
            assert contact_data["public_key"] == "cc" * 32

    @pytest.mark.asyncio
    async def test_returns_early_on_empty_public_key(self, test_db):
        """Handler exits without upserting when public_key is empty."""
        from app.event_handlers import on_new_contact

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"public_key": "", "adv_name": "Ghost"}

            await on_new_contact(MockEvent())

            mock_broadcast.assert_not_called()

            # No contacts should exist
            contacts = await ContactRepository.get_all()
            assert len(contacts) == 0

    @pytest.mark.asyncio
    async def test_returns_early_on_missing_public_key(self, test_db):
        """Handler exits without upserting when public_key field is absent."""
        from app.event_handlers import on_new_contact

        with patch("app.event_handlers.broadcast_event") as mock_broadcast:

            class MockEvent:
                payload = {"adv_name": "NoKey"}

            await on_new_contact(MockEvent())

            mock_broadcast.assert_not_called()

            contacts = await ContactRepository.get_all()
            assert len(contacts) == 0

    @pytest.mark.asyncio
    async def test_sets_on_radio_true(self, test_db):
        """Contact data passed to upsert has on_radio=True."""
        from app.event_handlers import on_new_contact

        with (
            patch("app.event_handlers.broadcast_event"),
            patch("app.event_handlers.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000

            class MockEvent:
                payload = {
                    "public_key": "dd" * 32,
                    "adv_name": "Delta",
                    "type": 0,
                    "flags": 0,
                }

            await on_new_contact(MockEvent())

            contact = await ContactRepository.get_by_key("dd" * 32)
            assert contact is not None
            assert contact.on_radio is True

    @pytest.mark.asyncio
    async def test_sets_last_seen_to_current_timestamp(self, test_db):
        """Contact data includes last_seen set to current time."""
        from app.event_handlers import on_new_contact

        with (
            patch("app.event_handlers.broadcast_event"),
            patch("app.event_handlers.time") as mock_time,
        ):
            mock_time.time.return_value = 1700099999

            class MockEvent:
                payload = {
                    "public_key": "ee" * 32,
                    "adv_name": "Echo",
                    "type": 0,
                    "flags": 0,
                }

            await on_new_contact(MockEvent())

            contact = await ContactRepository.get_by_key("ee" * 32)
            assert contact is not None
            assert contact.last_seen == 1700099999
