"""Tests for the contacts router.

Verifies the contact CRUD endpoints, sync, mark-read, delete,
and add/remove from radio operations.

Uses httpx.AsyncClient with real in-memory SQLite database.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from meshcore import EventType

from app.database import Database
from app.repository import ContactRepository

# Sample 64-char hex public keys for testing
KEY_A = "aa" * 32  # aaaa...aa
KEY_B = "bb" * 32  # bbbb...bb
KEY_C = "cc" * 32  # cccc...cc


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
