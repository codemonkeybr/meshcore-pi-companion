"""Tests for repository layer."""

from unittest.mock import patch

import pytest

from app.models import Contact, ContactUpsert
from app.repository import (
    AppSettingsRepository,
    ContactAdvertPathRepository,
    ContactNameHistoryRepository,
    ContactRepository,
    MessageRepository,
)


async def _create_message(test_db, **overrides) -> int:
    """Helper to insert a message and return its id."""
    defaults = {
        "msg_type": "CHAN",
        "text": "Hello",
        "conversation_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
        "sender_timestamp": 1700000000,
        "received_at": 1700000000,
    }
    defaults.update(overrides)
    msg_id = await MessageRepository.create(**defaults)
    assert msg_id is not None
    return msg_id


class TestMessageRepositoryAddPath:
    """Test MessageRepository.add_path against a real SQLite database."""

    @pytest.mark.asyncio
    async def test_add_path_to_message_with_no_existing_paths(self, test_db):
        """Adding a path to a message with no existing paths creates a new array."""
        msg_id = await _create_message(test_db)

        result = await MessageRepository.add_path(
            message_id=msg_id, path="1A2B", received_at=1700000000
        )

        assert len(result) == 1
        assert result[0].path == "1A2B"
        assert result[0].received_at == 1700000000

    @pytest.mark.asyncio
    async def test_add_path_to_message_with_existing_paths(self, test_db):
        """Adding a path to a message with existing paths appends to the array."""
        msg_id = await _create_message(test_db)

        await MessageRepository.add_path(message_id=msg_id, path="1A", received_at=1699999999)
        result = await MessageRepository.add_path(
            message_id=msg_id, path="2B3C", received_at=1700000000
        )

        assert len(result) == 2
        assert result[0].path == "1A"
        assert result[1].path == "2B3C"

    @pytest.mark.asyncio
    async def test_add_path_to_nonexistent_message_returns_empty(self, test_db):
        """Adding a path to a nonexistent message returns empty list."""
        result = await MessageRepository.add_path(
            message_id=999999, path="1A2B", received_at=1700000000
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_add_path_uses_current_time_if_not_provided(self, test_db):
        """Adding a path without received_at uses current timestamp."""
        msg_id = await _create_message(test_db)

        with patch("app.repository.messages.time") as mock_time:
            mock_time.time.return_value = 1700000500.5
            result = await MessageRepository.add_path(message_id=msg_id, path="1A2B")

        assert len(result) == 1
        assert result[0].received_at == 1700000500

    @pytest.mark.asyncio
    async def test_add_empty_path_for_direct_message(self, test_db):
        """Adding an empty path (direct message) works correctly."""
        msg_id = await _create_message(test_db)

        result = await MessageRepository.add_path(
            message_id=msg_id, path="", received_at=1700000000
        )

        assert len(result) == 1
        assert result[0].path == ""  # Empty path = direct
        assert result[0].received_at == 1700000000

    @pytest.mark.asyncio
    async def test_add_multiple_paths_accumulate(self, test_db):
        """Multiple add_path calls accumulate all paths."""
        msg_id = await _create_message(test_db)

        await MessageRepository.add_path(msg_id, "", received_at=1700000001)
        await MessageRepository.add_path(msg_id, "1A", received_at=1700000002)
        result = await MessageRepository.add_path(msg_id, "1A2B", received_at=1700000003)

        assert len(result) == 3
        assert result[0].path == ""
        assert result[1].path == "1A"
        assert result[2].path == "1A2B"


class TestMessageRepositoryGetByContent:
    """Test MessageRepository.get_by_content against a real SQLite database."""

    @pytest.mark.asyncio
    async def test_get_by_content_finds_matching_message(self, test_db):
        """Returns message when all content fields match."""
        msg_id = await _create_message(
            test_db,
            msg_type="CHAN",
            conversation_key="ABCD1234ABCD1234ABCD1234ABCD1234",
            text="Hello world",
            sender_timestamp=1700000000,
        )

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="ABCD1234ABCD1234ABCD1234ABCD1234",
            text="Hello world",
            sender_timestamp=1700000000,
        )

        assert result is not None
        assert result.id == msg_id
        assert result.type == "CHAN"
        assert result.text == "Hello world"

    @pytest.mark.asyncio
    async def test_get_by_content_returns_none_when_not_found(self, test_db):
        """Returns None when no message matches."""
        await _create_message(test_db, text="Existing message")

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
            text="Not found",
            sender_timestamp=1700000000,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_content_handles_null_sender_timestamp(self, test_db):
        """Handles messages with NULL sender_timestamp correctly."""
        msg_id = await _create_message(
            test_db,
            msg_type="PRIV",
            conversation_key="abc123abc123abc123abc123abc12300",
            text="Null timestamp msg",
            sender_timestamp=None,
            outgoing=True,
        )

        result = await MessageRepository.get_by_content(
            msg_type="PRIV",
            conversation_key="abc123abc123abc123abc123abc12300",
            text="Null timestamp msg",
            sender_timestamp=None,
        )

        assert result is not None
        assert result.id == msg_id
        assert result.sender_timestamp is None
        assert result.outgoing is True

    @pytest.mark.asyncio
    async def test_get_by_content_can_filter_incoming_vs_outgoing(self, test_db):
        """Outgoing filter keeps incoming duplicate reconciliation on the right row."""
        conversation_key = "abc123abc123abc123abc123abc12300"
        incoming_id = await _create_message(
            test_db,
            msg_type="PRIV",
            conversation_key=conversation_key,
            text="Same text",
            sender_timestamp=1700000000,
            outgoing=False,
        )
        outgoing_id = await _create_message(
            test_db,
            msg_type="PRIV",
            conversation_key=conversation_key,
            text="Same text",
            sender_timestamp=1700000000,
            outgoing=True,
        )

        incoming = await MessageRepository.get_by_content(
            msg_type="PRIV",
            conversation_key=conversation_key,
            text="Same text",
            sender_timestamp=1700000000,
            outgoing=False,
        )
        outgoing = await MessageRepository.get_by_content(
            msg_type="PRIV",
            conversation_key=conversation_key,
            text="Same text",
            sender_timestamp=1700000000,
            outgoing=True,
        )

        assert incoming is not None
        assert outgoing is not None
        assert incoming.id == incoming_id
        assert outgoing.id == outgoing_id

    @pytest.mark.asyncio
    async def test_get_by_content_distinguishes_by_timestamp(self, test_db):
        """Different sender_timestamps are distinguished correctly."""
        await _create_message(test_db, text="Same text", sender_timestamp=1700000000)
        msg_id2 = await _create_message(test_db, text="Same text", sender_timestamp=1700000001)

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
            text="Same text",
            sender_timestamp=1700000001,
        )

        assert result is not None
        assert result.id == msg_id2

    @pytest.mark.asyncio
    async def test_get_by_content_with_paths(self, test_db):
        """Returns message with paths correctly parsed."""
        msg_id = await _create_message(test_db, text="Multi-path message")
        await MessageRepository.add_path(msg_id, "1A2B", received_at=1700000000)
        await MessageRepository.add_path(msg_id, "3C4D", received_at=1700000001)

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
            text="Multi-path message",
            sender_timestamp=1700000000,
        )

        assert result is not None
        assert result.paths is not None
        assert len(result.paths) == 2
        assert result.paths[0].path == "1A2B"
        assert result.paths[1].path == "3C4D"

    @pytest.mark.asyncio
    async def test_get_by_content_recovers_from_corrupted_paths_json(self, test_db):
        """Malformed JSON in paths column returns message with paths=None."""
        msg_id = await _create_message(test_db, text="Corrupted paths")

        # Inject malformed JSON directly into the paths column
        await test_db.conn.execute(
            "UPDATE messages SET paths = ? WHERE id = ?",
            ("not valid json{{{", msg_id),
        )
        await test_db.conn.commit()

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
            text="Corrupted paths",
            sender_timestamp=1700000000,
        )

        assert result is not None
        assert result.id == msg_id
        assert result.paths is None

    @pytest.mark.asyncio
    async def test_get_by_content_recovers_from_paths_missing_keys(self, test_db):
        """Valid JSON but missing expected keys returns message with paths=None."""
        msg_id = await _create_message(test_db, text="Bad keys")

        # Valid JSON but missing "path" / "received_at" keys
        await test_db.conn.execute(
            "UPDATE messages SET paths = ? WHERE id = ?",
            ('[{"wrong_key": "value"}]', msg_id),
        )
        await test_db.conn.commit()

        result = await MessageRepository.get_by_content(
            msg_type="CHAN",
            conversation_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0",
            text="Bad keys",
            sender_timestamp=1700000000,
        )

        assert result is not None
        assert result.id == msg_id
        assert result.paths is None


class TestContactAdvertPathRepository:
    """Test storing and retrieving recent unique advert paths."""

    @pytest.mark.asyncio
    async def test_record_observation_upserts_and_tracks_count(self, test_db):
        repeater_key = "aa" * 32
        await ContactRepository.upsert({"public_key": repeater_key, "name": "R1", "type": 2})

        await ContactAdvertPathRepository.record_observation(repeater_key, "112233", 1000)
        await ContactAdvertPathRepository.record_observation(repeater_key, "112233", 1010)

        paths = await ContactAdvertPathRepository.get_recent_for_contact(repeater_key, limit=10)
        assert len(paths) == 1
        assert paths[0].path == "112233"
        assert paths[0].path_len == 3
        assert paths[0].next_hop == "11"
        assert paths[0].first_seen == 1000
        assert paths[0].last_seen == 1010
        assert paths[0].heard_count == 2

    @pytest.mark.asyncio
    async def test_record_observation_preserves_full_multibyte_next_hop(self, test_db):
        repeater_key = "ab" * 32
        await ContactRepository.upsert({"public_key": repeater_key, "name": "Rmulti", "type": 2})

        await ContactAdvertPathRepository.record_observation(
            repeater_key, "aa11bb22", 1000, hop_count=2
        )

        paths = await ContactAdvertPathRepository.get_recent_for_contact(repeater_key, limit=10)
        assert len(paths) == 1
        assert paths[0].next_hop == "aa11"

    @pytest.mark.asyncio
    async def test_same_path_hex_with_different_path_len_is_stored_separately(self, test_db):
        repeater_key = "ac" * 32
        await ContactRepository.upsert({"public_key": repeater_key, "name": "Rsplit", "type": 2})

        await ContactAdvertPathRepository.record_observation(
            repeater_key, "aa00", 1000, hop_count=1
        )
        await ContactAdvertPathRepository.record_observation(
            repeater_key, "aa00", 1010, hop_count=2
        )

        paths = await ContactAdvertPathRepository.get_recent_for_contact(repeater_key, limit=10)
        assert len(paths) == 2
        assert [(p.path, p.path_len, p.next_hop) for p in paths] == [
            ("aa00", 2, "aa"),
            ("aa00", 1, "aa00"),
        ]

    @pytest.mark.asyncio
    async def test_prune_distinguishes_same_path_hex_with_different_path_len(self, test_db):
        repeater_key = "ad" * 32
        await ContactRepository.upsert({"public_key": repeater_key, "name": "Rprune", "type": 2})

        await ContactAdvertPathRepository.record_observation(
            repeater_key, "aa00", 1000, max_paths=2, hop_count=1
        )
        await ContactAdvertPathRepository.record_observation(
            repeater_key, "aa00", 1001, max_paths=2, hop_count=2
        )
        await ContactAdvertPathRepository.record_observation(
            repeater_key, "bb00", 1002, max_paths=2, hop_count=1
        )

        paths = await ContactAdvertPathRepository.get_recent_for_contact(repeater_key, limit=10)
        assert [(p.path, p.path_len) for p in paths] == [("bb00", 1), ("aa00", 2)]

    @pytest.mark.asyncio
    async def test_prunes_to_most_recent_n_unique_paths(self, test_db):
        repeater_key = "bb" * 32
        await ContactRepository.upsert({"public_key": repeater_key, "name": "R2", "type": 2})

        await ContactAdvertPathRepository.record_observation(repeater_key, "aa", 1000, max_paths=2)
        await ContactAdvertPathRepository.record_observation(repeater_key, "bb", 1001, max_paths=2)
        await ContactAdvertPathRepository.record_observation(repeater_key, "cc", 1002, max_paths=2)

        paths = await ContactAdvertPathRepository.get_recent_for_contact(repeater_key, limit=10)
        assert [p.path for p in paths] == ["cc", "bb"]

    @pytest.mark.asyncio
    async def test_get_recent_for_all_repeaters_respects_limit(self, test_db):
        repeater_a = "cc" * 32
        repeater_b = "dd" * 32
        await ContactRepository.upsert({"public_key": repeater_a, "name": "RA", "type": 2})
        await ContactRepository.upsert({"public_key": repeater_b, "name": "RB", "type": 2})

        await ContactAdvertPathRepository.record_observation(repeater_a, "01", 1000)
        await ContactAdvertPathRepository.record_observation(repeater_a, "02", 1001)
        await ContactAdvertPathRepository.record_observation(repeater_b, "", 1002)

        grouped = await ContactAdvertPathRepository.get_recent_for_all_contacts(limit_per_contact=1)
        by_key = {item.public_key: item.paths for item in grouped}

        assert repeater_a in by_key
        assert repeater_b in by_key
        assert len(by_key[repeater_a]) == 1
        assert by_key[repeater_a][0].path == "02"
        assert by_key[repeater_b][0].path == ""
        assert by_key[repeater_b][0].next_hop is None


class TestContactNameHistoryRepository:
    """Test contact name history tracking."""

    @pytest.mark.asyncio
    async def test_record_and_retrieve_name_history(self, test_db):
        key = "aa" * 32
        await ContactRepository.upsert({"public_key": key, "name": "Alice", "type": 1})

        await ContactNameHistoryRepository.record_name(key, "Alice", 1000)
        await ContactNameHistoryRepository.record_name(key, "AliceV2", 2000)

        history = await ContactNameHistoryRepository.get_history(key)
        assert len(history) == 2
        assert history[0].name == "AliceV2"  # most recent first
        assert history[1].name == "Alice"

    @pytest.mark.asyncio
    async def test_record_name_upserts_last_seen(self, test_db):
        key = "bb" * 32
        await ContactRepository.upsert({"public_key": key, "name": "Bob", "type": 1})

        await ContactNameHistoryRepository.record_name(key, "Bob", 1000)
        await ContactNameHistoryRepository.record_name(key, "Bob", 2000)

        history = await ContactNameHistoryRepository.get_history(key)
        assert len(history) == 1
        assert history[0].first_seen == 1000
        assert history[0].last_seen == 2000


class TestMessageRepositoryContactStats:
    """Test per-contact message counting methods."""

    @pytest.mark.asyncio
    async def test_count_dm_messages(self, test_db):
        key = "aa" * 32
        await ContactRepository.upsert({"public_key": key, "name": "Alice", "type": 1})

        await MessageRepository.create(
            msg_type="PRIV",
            text="hi",
            conversation_key=key,
            sender_timestamp=1000,
            received_at=1000,
            sender_key=key,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="hello back",
            conversation_key=key,
            sender_timestamp=1001,
            received_at=1001,
            outgoing=True,
        )
        # Different contact's DM should not be counted
        other_key = "bb" * 32
        await MessageRepository.create(
            msg_type="PRIV",
            text="hey",
            conversation_key=other_key,
            sender_timestamp=1002,
            received_at=1002,
            sender_key=other_key,
        )

        count = await MessageRepository.count_dm_messages(key)
        assert count == 2

    @pytest.mark.asyncio
    async def test_count_channel_messages_by_sender(self, test_db):
        key = "aa" * 32
        chan_key = "CC" * 16

        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: msg1",
            conversation_key=chan_key,
            sender_timestamp=1000,
            received_at=1000,
            sender_name="Alice",
            sender_key=key,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: msg2",
            conversation_key=chan_key,
            sender_timestamp=1001,
            received_at=1001,
            sender_name="Alice",
            sender_key=key,
        )

        count = await MessageRepository.count_channel_messages_by_sender(key)
        assert count == 2

    @pytest.mark.asyncio
    async def test_get_most_active_rooms(self, test_db):
        key = "aa" * 32
        chan_a = "AA" * 16
        chan_b = "BB" * 16

        from app.repository import ChannelRepository

        await ChannelRepository.upsert(chan_a, "General")
        await ChannelRepository.upsert(chan_b, "Random")

        # 3 messages in chan_a, 1 in chan_b
        for i in range(3):
            await MessageRepository.create(
                msg_type="CHAN",
                text=f"Alice: msg{i}",
                conversation_key=chan_a,
                sender_timestamp=1000 + i,
                received_at=1000 + i,
                sender_name="Alice",
                sender_key=key,
            )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: hi",
            conversation_key=chan_b,
            sender_timestamp=2000,
            received_at=2000,
            sender_name="Alice",
            sender_key=key,
        )

        rooms = await MessageRepository.get_most_active_rooms(key, limit=5)
        assert len(rooms) == 2
        assert rooms[0][0] == chan_a  # most active first
        assert rooms[0][1] == "General"
        assert rooms[0][2] == 3
        assert rooms[1][2] == 1


class TestContactRepositoryResolvePrefixes:
    """Test batch prefix resolution."""

    @pytest.mark.asyncio
    async def test_resolves_unique_prefixes(self, test_db):
        key_a = "aa" * 32
        key_b = "bb" * 32
        await ContactRepository.upsert({"public_key": key_a, "name": "Alice", "type": 1})
        await ContactRepository.upsert({"public_key": key_b, "name": "Bob", "type": 1})

        result = await ContactRepository.resolve_prefixes(["aa", "bb"])
        assert "aa" in result
        assert "bb" in result
        assert result["aa"].public_key == key_a
        assert result["bb"].public_key == key_b

    @pytest.mark.asyncio
    async def test_omits_ambiguous_prefixes(self, test_db):
        key_a = "aa" + "11" * 31
        key_b = "aa" + "22" * 31
        await ContactRepository.upsert({"public_key": key_a, "name": "A1", "type": 1})
        await ContactRepository.upsert({"public_key": key_b, "name": "A2", "type": 1})

        result = await ContactRepository.resolve_prefixes(["aa"])
        assert "aa" not in result  # ambiguous — two matches

    @pytest.mark.asyncio
    async def test_empty_prefixes_returns_empty(self, test_db):
        result = await ContactRepository.resolve_prefixes([])
        assert result == {}


class TestContactRepositoryRecentQueries:
    """Test recent-contact selection helpers used for radio fill."""

    @pytest.mark.asyncio
    async def test_recently_advertised_includes_contacted_contacts(self, test_db):
        stale_contacted_fresh_advert = "ab" * 32
        advert_only = "cd" * 32
        repeater = "ef" * 32

        await ContactRepository.upsert(
            {
                "public_key": stale_contacted_fresh_advert,
                "name": "SeenAgain",
                "type": 1,
                "last_contacted": 100,
                "last_advert": 5000,
            }
        )
        await ContactRepository.upsert(
            {
                "public_key": advert_only,
                "name": "AdvertOnly",
                "type": 1,
                "last_advert": 4000,
            }
        )
        await ContactRepository.upsert(
            {
                "public_key": repeater,
                "name": "Repeater",
                "type": 2,
                "last_advert": 6000,
            }
        )

        contacts = await ContactRepository.get_recently_advertised_non_repeaters()

        assert [contact.public_key for contact in contacts] == [
            stale_contacted_fresh_advert,
            advert_only,
        ]


class TestAppSettingsRepository:
    """Test AppSettingsRepository parsing and migration edge cases."""

    @pytest.mark.asyncio
    async def test_get_handles_corrupted_json_and_invalid_sort_order(self, test_db):
        """Corrupted JSON fields are recovered with safe defaults.

        Uses the real DB so it exercises the lock-aware path. We stuff
        malformed JSON directly into the row, then verify ``get()`` recovers
        with defaults rather than propagating a parse error.
        """
        await test_db.conn.execute(
            """
            UPDATE app_settings
            SET max_radio_contacts = 250,
                auto_decrypt_dm_on_advert = 1,
                last_message_times = '{also-not-json',
                advert_interval = NULL,
                last_advert_time = NULL,
                flood_scope = '',
                blocked_keys = '[]',
                blocked_names = '[]',
                discovery_blocked_types = '[]'
            WHERE id = 1
            """
        )
        await test_db.conn.commit()

        settings = await AppSettingsRepository.get()

        assert settings.max_radio_contacts == 250
        assert settings.last_message_times == {}
        assert settings.advert_interval == 0
        assert settings.last_advert_time == 0

    @pytest.mark.asyncio
    async def test_get_in_conn_tolerates_missing_columns(self):
        """Defend against partial migrations where columns added by later
        migrations are absent from the row.

        Real DBs can't produce this state (schema init + migrations always
        run to the latest version on startup), but hand-rolled snapshots,
        external DB tools, or interrupted migrations might. The
        ``KeyError``-catching branches in ``_get_in_conn`` exist specifically
        to guarantee graceful degradation.

        We test these directly by mocking the connection boundary with a
        dict-backed row that mimics a pre-migration snapshot missing:
        - ``tracked_telemetry_repeaters`` (migration 53)
        - ``auto_resend_channel`` (migration 54)
        - ``telemetry_interval_hours`` (migration 57)
        """
        from unittest.mock import MagicMock

        from app.telemetry_interval import DEFAULT_TELEMETRY_INTERVAL_HOURS

        # sqlite3.Row raises KeyError for missing columns when accessed by
        # name, which is what we want to simulate. We mimic that here with a
        # dict-backed object whose __getitem__ raises KeyError for absent
        # keys (dict.__getitem__ already does this).
        class PartialRow(dict):
            def keys(self):  # pragma: no cover - aiosqlite.Row compat
                return super().keys()

        partial_row = PartialRow(
            {
                "max_radio_contacts": 123,
                "auto_decrypt_dm_on_advert": 1,
                "last_message_times": "{}",
                "advert_interval": 0,
                "last_advert_time": 0,
                "flood_scope": "",
                "blocked_keys": "[]",
                "blocked_names": "[]",
                "discovery_blocked_types": "[]",
                # intentionally missing: tracked_telemetry_repeaters,
                # auto_resend_channel, telemetry_interval_hours
            }
        )

        class FakeCursor:
            async def fetchone(self):
                return partial_row

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        mock_conn = MagicMock()
        mock_conn.execute = MagicMock(return_value=FakeCursor())

        settings = await AppSettingsRepository._get_in_conn(mock_conn)

        assert settings.max_radio_contacts == 123
        # Missing-column defaults kick in:
        assert settings.tracked_telemetry_repeaters == []
        assert settings.auto_resend_channel is False
        assert settings.telemetry_interval_hours == DEFAULT_TELEMETRY_INTERVAL_HOURS


class TestMessageRepositoryGetById:
    """Test MessageRepository.get_by_id method."""

    @pytest.mark.asyncio
    async def test_returns_message_when_exists(self, test_db):
        """Returns message for valid ID."""
        msg_id = await _create_message(test_db, text="Find me", outgoing=True)

        result = await MessageRepository.get_by_id(msg_id)

        assert result is not None
        assert result.id == msg_id
        assert result.text == "Find me"
        assert result.outgoing is True

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, test_db):
        """Returns None for nonexistent ID."""
        result = await MessageRepository.get_by_id(999999)

        assert result is None


class TestContactRepositoryUpsertContracts:
    @pytest.mark.asyncio
    async def test_accepts_contact_upsert_model(self, test_db):
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, name="Alice", type=1, on_radio=False)
        )

        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.name == "Alice"
        assert contact.type == 1

    @pytest.mark.asyncio
    async def test_accepts_contact_model(self, test_db):
        await ContactRepository.upsert(
            Contact(
                public_key="bb" * 32,
                name="Bob",
                type=2,
                on_radio=True,
                direct_path_hash_mode=-1,
            )
        )

        contact = await ContactRepository.get_by_key("bb" * 32)
        assert contact is not None
        assert contact.name == "Bob"
        assert contact.type == 2
        assert contact.on_radio is True


class TestContactRepositoryLastSeenSemantics:
    """Guard the 'last_seen = last RF reception' contract.

    Radio-driven contact-DB syncs must not clobber an earlier real RF timestamp,
    and callers that don't supply last_seen must leave the existing value alone.
    """

    @pytest.mark.asyncio
    async def test_upsert_without_last_seen_preserves_existing(self, test_db):
        real_rf_observation = 1_700_000_000
        await ContactRepository.upsert(
            ContactUpsert(
                public_key="aa" * 32,
                name="Alice",
                type=1,
                last_seen=real_rf_observation,
                on_radio=False,
            )
        )

        # A subsequent radio-sync style upsert (no last_seen supplied) must not
        # overwrite the real RF timestamp with now().
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, name="Alice", type=1, on_radio=False)
        )

        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == real_rf_observation

    @pytest.mark.asyncio
    async def test_upsert_monotonically_bumps_last_seen(self, test_db):
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_700_000_000, on_radio=False)
        )

        # Newer RF observation advances last_seen.
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_700_000_500, on_radio=False)
        )
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500

        # An older timestamp (out-of-order arrival) must not move it backwards.
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_699_999_000, on_radio=False)
        )
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500

    @pytest.mark.asyncio
    async def test_upsert_inserts_null_last_seen_when_not_supplied(self, test_db):
        # A radio-sync-only contact (never heard on RF) should have last_seen=NULL.
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, name="Alice", type=1, on_radio=False)
        )

        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen is None

    @pytest.mark.asyncio
    async def test_touch_last_seen_bumps_monotonically(self, test_db):
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_700_000_000, on_radio=False)
        )

        await ContactRepository.touch_last_seen("aa" * 32, 1_700_000_500)
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500

        # Older timestamps never move last_seen backwards.
        await ContactRepository.touch_last_seen("aa" * 32, 1_699_999_000)
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500

    @pytest.mark.asyncio
    async def test_update_last_contacted_does_not_touch_last_seen(self, test_db):
        # last_contacted = we sent TO them. It must not forge RF reception.
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_700_000_000, on_radio=False)
        )

        await ContactRepository.update_last_contacted("aa" * 32, 1_700_500_000)

        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_contacted == 1_700_500_000
        assert contact.last_seen == 1_700_000_000

    @pytest.mark.asyncio
    async def test_update_direct_path_bumps_last_seen_monotonically(self, test_db):
        # update_direct_path is driven by RF PATH reception on both callers
        # (packet processor + firmware PATH_UPDATE, which only fires from
        # onContactPathRecv during RF reception). It should advance last_seen
        # forward-only.
        await ContactRepository.upsert(
            ContactUpsert(public_key="aa" * 32, last_seen=1_700_000_000, on_radio=False)
        )

        await ContactRepository.update_direct_path(
            "aa" * 32, path="ab", path_len=1, path_hash_mode=0, updated_at=1_700_000_500
        )
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500
        assert contact.direct_path == "ab"

        # Out-of-order PATH arrival with an older timestamp must not rewind.
        await ContactRepository.update_direct_path(
            "aa" * 32, path="cd", path_len=1, path_hash_mode=0, updated_at=1_699_999_000
        )
        contact = await ContactRepository.get_by_key("aa" * 32)
        assert contact is not None
        assert contact.last_seen == 1_700_000_500
        # The path itself still updates — only last_seen is monotonic-guarded.
        assert contact.direct_path == "cd"
