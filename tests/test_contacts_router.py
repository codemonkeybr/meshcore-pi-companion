"""Tests for the contacts router.

Verifies the live contact CRUD, analytics, mark-read, delete,
historical decrypt, and routing override endpoints.

Uses httpx.AsyncClient with real in-memory SQLite database.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType

from app.repository import ContactAdvertPathRepository, ContactRepository, MessageRepository

# Sample 64-char hex public keys for testing
KEY_A = "aa" * 32  # aaaa...aa
KEY_B = "bb" * 32  # bbbb...bb
KEY_C = "cc" * 32  # cccc...cc


def _noop_radio_operation(mc=None):
    """Factory for a no-op radio_operation context manager that yields mc."""

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield mc

    return _ctx


async def _insert_contact(public_key=KEY_A, name="Alice", on_radio=False, **overrides):
    """Insert a contact into the test database."""
    data = {
        "public_key": public_key,
        "name": name,
        "type": 0,
        "flags": 0,
        "last_path": None,
        "last_path_len": -1,
        "last_advert": None,
        "lat": None,
        "lon": None,
        "last_seen": None,
        "on_radio": on_radio,
        "last_contacted": None,
        "first_seen": None,
    }
    data.update(overrides)
    await ContactRepository.upsert(data)


class TestListContacts:
    """Test GET /api/contacts."""

    @pytest.mark.asyncio
    async def test_list_returns_contacts(self, test_db, client):
        await _insert_contact(KEY_A, "Alice")
        await _insert_contact(KEY_B, "Bob")

        response = await client.get("/api/contacts")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        keys = {d["public_key"] for d in data}
        assert KEY_A in keys
        assert KEY_B in keys

    @pytest.mark.asyncio
    async def test_list_pagination_params(self, test_db, client):
        # Insert 3 contacts
        await _insert_contact(KEY_A, "Alice")
        await _insert_contact(KEY_B, "Bob")
        await _insert_contact(KEY_C, "Carol")

        response = await client.get("/api/contacts?limit=2&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2


class TestCreateContact:
    """Test POST /api/contacts."""

    @pytest.mark.asyncio
    async def test_create_new_contact(self, test_db, client):
        with patch("app.websocket.broadcast_event") as mock_broadcast:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "NewContact"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["public_key"] == KEY_A
        assert data["name"] == "NewContact"
        assert data["last_seen"] is not None

        # Verify in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.name == "NewContact"
        assert data["last_seen"] == contact.last_seen
        mock_broadcast.assert_called_once_with("contact", contact.model_dump())

    @pytest.mark.asyncio
    async def test_create_invalid_hex(self, test_db, client):
        """Non-hex public key returns 400."""
        response = await client.post(
            "/api/contacts",
            json={"public_key": "zz" * 32, "name": "Bad"},
        )

        assert response.status_code == 400
        assert "hex" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_short_key_rejected(self, test_db, client):
        """Key shorter than 64 chars is rejected by pydantic validation."""
        response = await client.post(
            "/api/contacts",
            json={"public_key": "aa" * 16, "name": "Short"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_existing_updates_name(self, test_db, client):
        """Creating a contact that exists updates the name."""
        await _insert_contact(KEY_A, "OldName")

        response = await client.post(
            "/api/contacts",
            json={"public_key": KEY_A, "name": "NewName"},
        )

        assert response.status_code == 200
        # Verify name was updated in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.name == "NewName"


class TestAdvertPaths:
    """Test repeater advert path endpoints."""

    @pytest.mark.asyncio
    async def test_list_repeater_advert_paths(self, test_db, client):
        repeater_key = KEY_A
        await _insert_contact(repeater_key, "R1", type=2)
        await ContactAdvertPathRepository.record_observation(repeater_key, "1122", 1000)
        await ContactAdvertPathRepository.record_observation(repeater_key, "3344", 1010)

        response = await client.get("/api/contacts/repeaters/advert-paths?limit_per_repeater=1")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["public_key"] == repeater_key
        assert len(data[0]["paths"]) == 1
        assert data[0]["paths"][0]["path"] == "3344"
        assert data[0]["paths"][0]["next_hop"] == "33"


class TestContactAnalytics:
    """Test GET /api/contacts/analytics."""

    @pytest.mark.asyncio
    async def test_analytics_returns_keyed_contact_profile_and_series(self, test_db, client):
        now = 2_000_000_000
        chan_key = "11" * 16
        await _insert_contact(KEY_A, "Alice", type=1)

        await MessageRepository.create(
            msg_type="PRIV",
            text="hi",
            conversation_key=KEY_A,
            sender_timestamp=now - 100,
            received_at=now - 100,
            sender_key=KEY_A,
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: ping",
            conversation_key=chan_key,
            sender_timestamp=now - 7200,
            received_at=now - 7200,
            sender_name="Alice",
            sender_key=KEY_A,
        )

        with patch("app.repository.messages.time.time", return_value=now):
            response = await client.get("/api/contacts/analytics", params={"public_key": KEY_A})

        assert response.status_code == 200
        data = response.json()
        assert data["lookup_type"] == "contact"
        assert data["contact"]["public_key"] == KEY_A
        assert data["includes_direct_messages"] is True
        assert data["dm_message_count"] == 1
        assert data["channel_message_count"] == 1
        assert len(data["hourly_activity"]) == 24
        assert len(data["weekly_activity"]) == 26
        assert sum(bucket["last_24h_count"] for bucket in data["hourly_activity"]) == 2
        assert sum(bucket["message_count"] for bucket in data["weekly_activity"]) == 2

    @pytest.mark.asyncio
    async def test_analytics_returns_name_only_profile_and_series(self, test_db, client):
        now = 2_000_000_000
        chan_key = "22" * 16

        await MessageRepository.create(
            msg_type="CHAN",
            text="Mystery: hi",
            conversation_key=chan_key,
            sender_timestamp=now - 100,
            received_at=now - 100,
            sender_name="Mystery",
        )
        await MessageRepository.create(
            msg_type="CHAN",
            text="Mystery: hello",
            conversation_key=chan_key,
            sender_timestamp=now - 86400,
            received_at=now - 86400,
            sender_name="Mystery",
        )

        with patch("app.repository.messages.time.time", return_value=now):
            response = await client.get("/api/contacts/analytics", params={"name": "Mystery"})

        assert response.status_code == 200
        data = response.json()
        assert data["lookup_type"] == "name"
        assert data["contact"] is None
        assert data["name"] == "Mystery"
        assert data["name_first_seen_at"] == now - 86400
        assert data["includes_direct_messages"] is False
        assert data["dm_message_count"] == 0
        assert data["channel_message_count"] == 2
        assert len(data["hourly_activity"]) == 24
        assert len(data["weekly_activity"]) == 26
        assert sum(bucket["last_24h_count"] for bucket in data["hourly_activity"]) == 1
        assert sum(bucket["message_count"] for bucket in data["weekly_activity"]) == 2

    @pytest.mark.asyncio
    async def test_analytics_requires_exactly_one_lookup_mode(self, test_db, client):
        response = await client.get(
            "/api/contacts/analytics",
            params={"public_key": KEY_A, "name": "Alice"},
        )
        assert response.status_code == 400
        assert "exactly one" in response.json()["detail"].lower()


class TestDeleteContactCascade:
    """Test that contact delete cleans up related tables."""

    @pytest.mark.asyncio
    async def test_delete_removes_name_history_and_advert_paths(self, test_db, client):
        await _insert_contact(KEY_A, "Alice")

        from app.repository import ContactNameHistoryRepository

        await ContactNameHistoryRepository.record_name(KEY_A, "Alice", 1000)
        await ContactAdvertPathRepository.record_observation(KEY_A, "1122", 1000)

        # Verify data exists
        assert len(await ContactNameHistoryRepository.get_history(KEY_A)) == 1
        assert len(await ContactAdvertPathRepository.get_recent_for_contact(KEY_A)) == 1

        with patch("app.routers.contacts.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None
            mock_rm.radio_operation = _noop_radio_operation()

            response = await client.delete(f"/api/contacts/{KEY_A}")

        assert response.status_code == 200

        # Verify related data cleaned up
        assert len(await ContactNameHistoryRepository.get_history(KEY_A)) == 0
        assert len(await ContactAdvertPathRepository.get_recent_for_contact(KEY_A)) == 0


class TestMarkRead:
    """Test POST /api/contacts/{public_key}/mark-read."""

    @pytest.mark.asyncio
    async def test_mark_read_updates_timestamp(self, test_db, client):
        await _insert_contact(KEY_A)

        response = await client.post(f"/api/contacts/{KEY_A}/mark-read")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify last_read_at was set in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.last_read_at is not None

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self, test_db, client):
        response = await client.post(f"/api/contacts/{KEY_A}/mark-read")

        assert response.status_code == 404


class TestDeleteContact:
    """Test DELETE /api/contacts/{public_key}."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, test_db, client):
        await _insert_contact(KEY_A)

        with patch("app.routers.contacts.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None
            mock_rm.radio_operation = _noop_radio_operation()

            response = await client.delete(f"/api/contacts/{KEY_A}")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify deleted from DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, test_db, client):
        response = await client.delete(f"/api/contacts/{KEY_A}")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_removes_from_radio_if_connected(self, test_db, client):
        """When radio is connected and contact is on radio, remove it first."""
        await _insert_contact(KEY_A, on_radio=True)
        mock_radio_contact = MagicMock()

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=mock_radio_contact)
        mock_mc.commands.remove_contact = AsyncMock()

        with patch("app.routers.contacts.radio_manager") as mock_rm:
            mock_rm.is_connected = True
            mock_rm.meshcore = mock_mc
            mock_rm.radio_operation = _noop_radio_operation(mock_mc)

            response = await client.delete(f"/api/contacts/{KEY_A}")

        assert response.status_code == 200
        mock_mc.commands.remove_contact.assert_called_once_with(mock_radio_contact)


class TestCreateContactWithHistorical:
    """Test POST /api/contacts with try_historical=true."""

    @pytest.mark.asyncio
    async def test_new_contact_triggers_historical_decrypt(self, test_db, client):
        """Creating a new contact with try_historical triggers DM decryption."""
        with patch(
            "app.routers.contacts.start_historical_dm_decryption", new_callable=AsyncMock
        ) as mock_start:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "Alice", "try_historical": True},
            )

        assert response.status_code == 200
        assert response.json()["public_key"] == KEY_A

        mock_start.assert_awaited_once()
        # Verify correct args: (background_tasks, public_key, name)
        call_args = mock_start.call_args
        assert call_args[0][1] == KEY_A  # public_key
        assert call_args[0][2] == "Alice"  # display_name

    @pytest.mark.asyncio
    async def test_new_contact_without_historical(self, test_db, client):
        """Creating a new contact without try_historical does not trigger decryption."""
        with patch(
            "app.routers.contacts.start_historical_dm_decryption", new_callable=AsyncMock
        ) as mock_start:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "Alice", "try_historical": False},
            )

        assert response.status_code == 200
        mock_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_contact_with_historical(self, test_db, client):
        """Existing contact with try_historical still triggers decryption."""
        await _insert_contact(KEY_A, "Alice")

        with patch(
            "app.routers.contacts.start_historical_dm_decryption", new_callable=AsyncMock
        ) as mock_start:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "Alice", "try_historical": True},
            )

        assert response.status_code == 200
        mock_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_contact_updates_name_and_decrypts(self, test_db, client):
        """Existing contact with try_historical updates name AND triggers decryption."""
        await _insert_contact(KEY_A, "OldName")

        with patch(
            "app.routers.contacts.start_historical_dm_decryption", new_callable=AsyncMock
        ) as mock_start:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "NewName", "try_historical": True},
            )

        assert response.status_code == 200
        mock_start.assert_awaited_once()

        # Verify name was also updated
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.name == "NewName"

    @pytest.mark.asyncio
    async def test_default_try_historical_is_false(self, test_db, client):
        """try_historical defaults to false when not provided."""
        with patch(
            "app.routers.contacts.start_historical_dm_decryption", new_callable=AsyncMock
        ) as mock_start:
            response = await client.post(
                "/api/contacts",
                json={"public_key": KEY_A, "name": "Alice"},
            )

        assert response.status_code == 200
        mock_start.assert_not_awaited()


class TestRoutingOverride:
    """Test POST /api/contacts/{public_key}/routing-override."""

    @pytest.mark.asyncio
    async def test_set_explicit_routing_override(self, test_db, client):
        await _insert_contact(KEY_A, last_path="11", last_path_len=1, out_path_hash_mode=0)

        with (
            patch("app.routers.contacts.radio_manager") as mock_rm,
            patch("app.websocket.broadcast_event") as mock_broadcast,
        ):
            mock_rm.is_connected = False
            response = await client.post(
                f"/api/contacts/{KEY_A}/routing-override",
                json={"route": "ae92,f13e"},
            )

        assert response.status_code == 200
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.last_path == "11"
        assert contact.last_path_len == 1
        assert contact.route_override_path == "ae92f13e"
        assert contact.route_override_len == 2
        assert contact.route_override_hash_mode == 1
        mock_broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_flood_routing_override_pushes_effective_route(self, test_db, client):
        await _insert_contact(
            KEY_A,
            on_radio=True,
            last_path="11",
            last_path_len=1,
            out_path_hash_mode=0,
        )

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        with (
            patch("app.routers.contacts.radio_manager") as mock_rm,
            patch("app.websocket.broadcast_event"),
        ):
            mock_rm.is_connected = True
            mock_rm.radio_operation = _noop_radio_operation(mock_mc)
            response = await client.post(
                f"/api/contacts/{KEY_A}/routing-override",
                json={"route": "-1"},
            )

        assert response.status_code == 200
        payload = mock_mc.commands.add_contact.call_args.args[0]
        assert payload["out_path"] == ""
        assert payload["out_path_len"] == -1
        assert payload["out_path_hash_mode"] == -1

        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.route_override_len == -1
        assert contact.last_path == "11"
        assert contact.last_path_len == 1

    @pytest.mark.asyncio
    async def test_blank_route_clears_override_and_resets_learned_path(self, test_db, client):
        await _insert_contact(
            KEY_A,
            last_path="11",
            last_path_len=1,
            out_path_hash_mode=0,
            route_override_path="ae92f13e",
            route_override_len=2,
            route_override_hash_mode=1,
        )

        with (
            patch("app.routers.contacts.radio_manager") as mock_rm,
            patch("app.websocket.broadcast_event"),
        ):
            mock_rm.is_connected = False
            response = await client.post(
                f"/api/contacts/{KEY_A}/routing-override",
                json={"route": ""},
            )

        assert response.status_code == 200
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.route_override_len is None
        assert contact.last_path == ""
        assert contact.last_path_len == -1
        assert contact.out_path_hash_mode == -1

    @pytest.mark.asyncio
    async def test_rejects_invalid_explicit_route(self, test_db, client):
        await _insert_contact(KEY_A)

        response = await client.post(
            f"/api/contacts/{KEY_A}/routing-override",
            json={"route": "ae,f13e"},
        )

        assert response.status_code == 400
        assert "same width" in response.json()["detail"].lower()
