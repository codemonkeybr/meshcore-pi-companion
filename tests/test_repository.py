"""Tests for repository layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repository import (
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


class TestAppSettingsRepository:
    """Test AppSettingsRepository parsing and migration edge cases."""

    @pytest.mark.asyncio
    async def test_get_handles_corrupted_json_and_invalid_sort_order(self):
        """Corrupted JSON fields are recovered with safe defaults."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value={
                "max_radio_contacts": 250,
                "favorites": "{not-json",
                "auto_decrypt_dm_on_advert": 1,
                "sidebar_sort_order": "invalid",
                "last_message_times": "{also-not-json",
                "preferences_migrated": 0,
                "advert_interval": None,
                "last_advert_time": None,
                "bots": "{bad-bots-json",
                "mqtt_broker_host": "",
                "mqtt_broker_port": 1883,
                "mqtt_username": "",
                "mqtt_password": "",
                "mqtt_use_tls": 0,
                "mqtt_tls_insecure": 0,
                "mqtt_topic_prefix": "meshcore",
                "mqtt_publish_messages": 0,
                "mqtt_publish_raw_packets": 0,
                "community_mqtt_enabled": 0,
                "community_mqtt_iata": "",
                "community_mqtt_broker_host": "mqtt-us-v1.letsmesh.net",
                "community_mqtt_broker_port": 443,
                "community_mqtt_email": "",
            }
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_db = MagicMock()
        mock_db.conn = mock_conn

        with patch("app.repository.settings.db", mock_db):
            from app.repository import AppSettingsRepository

            settings = await AppSettingsRepository.get()

        assert settings.max_radio_contacts == 250
        assert settings.favorites == []
        assert settings.last_message_times == {}
        assert settings.sidebar_sort_order == "recent"
        assert settings.bots == []
        assert settings.advert_interval == 0
        assert settings.last_advert_time == 0

    @pytest.mark.asyncio
    async def test_add_favorite_is_idempotent(self):
        """Adding an existing favorite does not write duplicate entries."""
        from app.models import AppSettings, Favorite

        existing = AppSettings(favorites=[Favorite(type="contact", id="aa" * 32)])

        with (
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=existing,
            ),
            patch(
                "app.repository.AppSettingsRepository.update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            from app.repository import AppSettingsRepository

            result = await AppSettingsRepository.add_favorite("contact", "aa" * 32)

        assert result == existing
        mock_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_migrate_preferences_uses_recent_for_invalid_sort_order(self):
        """Migration normalizes invalid sort order to 'recent'."""
        from app.models import AppSettings

        current = AppSettings(preferences_migrated=False)
        migrated = AppSettings(preferences_migrated=True, sidebar_sort_order="recent")

        with (
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=current,
            ),
            patch(
                "app.repository.AppSettingsRepository.update",
                new_callable=AsyncMock,
                return_value=migrated,
            ) as mock_update,
        ):
            from app.repository import AppSettingsRepository

            result, did_migrate = await AppSettingsRepository.migrate_preferences_from_frontend(
                favorites=[{"type": "contact", "id": "bb" * 32}],
                sort_order="weird-order",
                last_message_times={"contact-bbbbbbbbbbbb": 123},
            )

        assert did_migrate is True
        assert result.preferences_migrated is True
        assert mock_update.call_args.kwargs["sidebar_sort_order"] == "recent"
        assert mock_update.call_args.kwargs["preferences_migrated"] is True


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
