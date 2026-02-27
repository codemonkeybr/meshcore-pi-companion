"""Tests for the contacts router.

Verifies the contact CRUD endpoints, sync, mark-read, delete,
and add/remove from radio operations.

Uses httpx.AsyncClient with real in-memory SQLite database.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from meshcore import EventType

from app.database import Database
from app.radio import radio_manager
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


@pytest.fixture(autouse=True)
def _reset_radio_state():
    """Save/restore radio_manager state so tests don't leak."""
    prev = radio_manager._meshcore
    prev_lock = radio_manager._operation_lock
    yield
    radio_manager._meshcore = prev
    radio_manager._operation_lock = prev_lock


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


@pytest.fixture
def client():
    """Create an httpx AsyncClient for testing the app."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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
        response = await client.post(
            "/api/contacts",
            json={"public_key": KEY_A, "name": "NewContact"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["public_key"] == KEY_A
        assert data["name"] == "NewContact"

        # Verify in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact is not None
        assert contact.name == "NewContact"

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


class TestGetContact:
    """Test GET /api/contacts/{public_key}."""

    @pytest.mark.asyncio
    async def test_get_existing(self, test_db, client):
        await _insert_contact(KEY_A, "Alice")

        response = await client.get(f"/api/contacts/{KEY_A}")

        assert response.status_code == 200
        assert response.json()["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_not_found(self, test_db, client):
        response = await client.get(f"/api/contacts/{KEY_A}")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_ambiguous_prefix_returns_409(self, test_db, client):
        # Insert two contacts that share a prefix
        await _insert_contact("abcd12" + "00" * 29, "ContactA")
        await _insert_contact("abcd12" + "ff" * 29, "ContactB")

        response = await client.get("/api/contacts/abcd12")

        assert response.status_code == 409
        assert "ambiguous" in response.json()["detail"].lower()


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

    @pytest.mark.asyncio
    async def test_get_contact_advert_paths_for_repeater(self, test_db, client):
        repeater_key = KEY_A
        await _insert_contact(repeater_key, "R1", type=2)
        await ContactAdvertPathRepository.record_observation(repeater_key, "", 1000)

        response = await client.get(f"/api/contacts/{repeater_key}/advert-paths")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["path"] == ""
        assert data[0]["next_hop"] is None

    @pytest.mark.asyncio
    async def test_get_contact_advert_paths_works_for_non_repeater(self, test_db, client):
        await _insert_contact(KEY_A, "Alice", type=1)

        response = await client.get(f"/api/contacts/{KEY_A}/advert-paths")

        assert response.status_code == 200
        assert response.json() == []


class TestContactDetail:
    """Test GET /api/contacts/{public_key}/detail."""

    @pytest.mark.asyncio
    async def test_detail_returns_full_profile(self, test_db, client):
        """Happy path: contact with DMs, channel messages, name history, advert paths."""
        await _insert_contact(KEY_A, "Alice", type=1)

        # Add some DMs
        await MessageRepository.create(
            msg_type="PRIV",
            text="hi",
            conversation_key=KEY_A,
            sender_timestamp=1000,
            received_at=1000,
            sender_key=KEY_A,
        )
        await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key=KEY_A,
            sender_timestamp=1001,
            received_at=1001,
            outgoing=True,
        )

        # Add a channel message attributed to this contact
        from app.repository import ContactNameHistoryRepository

        await MessageRepository.create(
            msg_type="CHAN",
            text="Alice: yo",
            conversation_key="CHAN_KEY_0" * 2,
            sender_timestamp=1002,
            received_at=1002,
            sender_name="Alice",
            sender_key=KEY_A,
        )

        # Record name history
        await ContactNameHistoryRepository.record_name(KEY_A, "Alice", 1000)
        await ContactNameHistoryRepository.record_name(KEY_A, "AliceOld", 500)

        # Record advert paths
        await ContactAdvertPathRepository.record_observation(KEY_A, "1122", 1000)
        await ContactAdvertPathRepository.record_observation(KEY_A, "", 900)

        response = await client.get(f"/api/contacts/{KEY_A}/detail")

        assert response.status_code == 200
        data = response.json()
        assert data["contact"]["public_key"] == KEY_A
        assert data["dm_message_count"] == 2
        assert data["channel_message_count"] == 1
        assert len(data["name_history"]) == 2
        assert data["name_history"][0]["name"] == "Alice"  # most recent first
        assert len(data["advert_paths"]) == 2
        assert len(data["most_active_rooms"]) == 1

    @pytest.mark.asyncio
    async def test_detail_contact_not_found(self, test_db, client):
        response = await client.get(f"/api/contacts/{KEY_A}/detail")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_with_no_activity(self, test_db, client):
        """Contact with no messages or paths returns zero counts and empty lists."""
        await _insert_contact(KEY_A, "Alice")

        response = await client.get(f"/api/contacts/{KEY_A}/detail")

        assert response.status_code == 200
        data = response.json()
        assert data["dm_message_count"] == 0
        assert data["channel_message_count"] == 0
        assert data["most_active_rooms"] == []
        assert data["advert_paths"] == []
        assert data["advert_frequency"] is None
        assert data["nearest_repeaters"] == []

    @pytest.mark.asyncio
    async def test_detail_nearest_repeaters_resolved(self, test_db, client):
        """Nearest repeaters are resolved from first-hop prefixes in advert paths."""
        await _insert_contact(KEY_A, "Alice", type=1)
        # Create a repeater whose key starts with "bb"
        await _insert_contact(KEY_B, "Relay1", type=2)

        # Record advert paths that go through KEY_B's prefix
        await ContactAdvertPathRepository.record_observation(KEY_A, "bb1122", 1000)
        await ContactAdvertPathRepository.record_observation(KEY_A, "bb3344", 1010)

        response = await client.get(f"/api/contacts/{KEY_A}/detail")

        assert response.status_code == 200
        data = response.json()
        assert len(data["nearest_repeaters"]) == 1
        repeater = data["nearest_repeaters"][0]
        assert repeater["public_key"] == KEY_B
        assert repeater["name"] == "Relay1"
        assert repeater["heard_count"] == 2

    @pytest.mark.asyncio
    async def test_detail_advert_frequency_computed(self, test_db, client):
        """Advert frequency is computed from path observations over time span."""
        await _insert_contact(KEY_A, "Alice")

        # 10 observations over 1 hour (3600s)
        for i in range(10):
            path_hex = f"{i:02x}" * 2  # unique paths to avoid upsert
            await ContactAdvertPathRepository.record_observation(KEY_A, path_hex, 1000 + i * 360)

        response = await client.get(f"/api/contacts/{KEY_A}/detail")

        assert response.status_code == 200
        data = response.json()
        # 10 observations / (3240s / 3600) ≈ 11.11/hr
        assert data["advert_frequency"] is not None
        assert data["advert_frequency"] > 0


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


class TestSyncContacts:
    """Test POST /api/contacts/sync."""

    @pytest.mark.asyncio
    async def test_sync_from_radio(self, test_db, client):
        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_result.payload = {
            KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0},
            KEY_B: {"adv_name": "Bob", "type": 1, "flags": 0},
        }
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post("/api/contacts/sync")

        assert response.status_code == 200
        assert response.json()["synced"] == 2

        # Verify contacts are in real DB
        alice = await ContactRepository.get_by_key(KEY_A)
        assert alice is not None
        assert alice.name == "Alice"

    @pytest.mark.asyncio
    async def test_sync_requires_connection(self, test_db, client):
        with patch("app.dependencies.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            response = await client.post("/api/contacts/sync")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_sync_claims_prefix_messages(self, test_db, client):
        """Syncing contacts promotes prefix-stored DM messages to the full key."""
        await MessageRepository.create(
            msg_type="PRIV",
            text="hello from prefix",
            received_at=1700000000,
            conversation_key=KEY_A[:12],
            sender_timestamp=1700000000,
        )

        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_result.payload = {KEY_A: {"adv_name": "Alice", "type": 1, "flags": 0}}
        mock_mc.commands.get_contacts = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post("/api/contacts/sync")

        assert response.status_code == 200
        assert response.json()["synced"] == 1

        messages = await MessageRepository.get_all(conversation_key=KEY_A)
        assert len(messages) == 1
        assert messages[0].conversation_key == KEY_A.lower()


class TestAddRemoveRadio:
    """Test add-to-radio and remove-from-radio endpoints."""

    @pytest.mark.asyncio
    async def test_add_to_radio(self, test_db, client):
        await _insert_contact(KEY_A)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=None)  # Not on radio
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.add_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post(f"/api/contacts/{KEY_A}/add-to-radio")

        assert response.status_code == 200
        mock_mc.commands.add_contact.assert_called_once()

        # Verify on_radio flag updated in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.on_radio is True

    @pytest.mark.asyncio
    async def test_add_already_on_radio(self, test_db, client):
        """Adding a contact already on radio returns ok without calling add_contact."""
        await _insert_contact(KEY_A, on_radio=True)

        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=MagicMock())  # On radio

        radio_manager._meshcore = mock_mc
        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post(f"/api/contacts/{KEY_A}/add-to-radio")

        assert response.status_code == 200
        assert "already" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_remove_from_radio(self, test_db, client):
        await _insert_contact(KEY_A, on_radio=True)

        mock_radio_contact = MagicMock()
        mock_mc = MagicMock()
        mock_mc.get_contact_by_key_prefix = MagicMock(return_value=mock_radio_contact)
        mock_result = MagicMock()
        mock_result.type = EventType.OK
        mock_mc.commands.remove_contact = AsyncMock(return_value=mock_result)

        radio_manager._meshcore = mock_mc
        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post(f"/api/contacts/{KEY_A}/remove-from-radio")

        assert response.status_code == 200
        mock_mc.commands.remove_contact.assert_called_once_with(mock_radio_contact)

        # Verify on_radio flag updated in DB
        contact = await ContactRepository.get_by_key(KEY_A)
        assert contact.on_radio is False

    @pytest.mark.asyncio
    async def test_add_requires_connection(self, test_db, client):
        with patch("app.dependencies.radio_manager") as mock_rm:
            mock_rm.is_connected = False
            mock_rm.meshcore = None

            response = await client.post(f"/api/contacts/{KEY_A}/add-to-radio")

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_remove_not_found(self, test_db, client):
        mock_mc = MagicMock()

        with patch("app.dependencies.radio_manager") as mock_dep_rm:
            mock_dep_rm.is_connected = True
            mock_dep_rm.meshcore = mock_mc

            response = await client.post(f"/api/contacts/{KEY_A}/remove-from-radio")

        assert response.status_code == 404
