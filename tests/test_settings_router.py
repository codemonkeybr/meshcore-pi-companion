"""Tests for settings router endpoints and validation behavior."""

import pytest
from fastapi import HTTPException

from app.database import Database
from app.models import AppSettings, BotConfig
from app.repository import AppSettingsRepository
from app.routers.settings import (
    AppSettingsUpdate,
    FavoriteRequest,
    MigratePreferencesRequest,
    migrate_preferences,
    toggle_favorite,
    update_settings,
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


class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self, test_db):
        result = await update_settings(
            AppSettingsUpdate(
                max_radio_contacts=321,
                advert_interval=3600,
                experimental_channel_double_send=True,
            )
        )

        assert result.max_radio_contacts == 321
        assert result.advert_interval == 3600
        assert result.experimental_channel_double_send is True

    @pytest.mark.asyncio
    async def test_empty_patch_returns_current_settings(self, test_db):
        result = await update_settings(AppSettingsUpdate())

        # Should return default settings without error
        assert isinstance(result, AppSettings)
        assert result.max_radio_contacts == 200  # default

    @pytest.mark.asyncio
    async def test_invalid_bot_syntax_returns_400(self):
        bad_bot = BotConfig(
            id="bot-1",
            name="BadBot",
            enabled=True,
            code="def bot(:\n    return 'x'\n",
        )

        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(bots=[bad_bot]))

        assert exc.value.status_code == 400
        assert "syntax error" in exc.value.detail.lower()


class TestToggleFavorite:
    @pytest.mark.asyncio
    async def test_adds_when_not_favorited(self, test_db):
        request = FavoriteRequest(type="contact", id="aa" * 32)
        result = await toggle_favorite(request)

        assert len(result.favorites) == 1
        assert result.favorites[0].type == "contact"
        assert result.favorites[0].id == "aa" * 32

    @pytest.mark.asyncio
    async def test_removes_when_already_favorited(self, test_db):
        # Pre-add a favorite
        await AppSettingsRepository.add_favorite("channel", "ABCD")

        request = FavoriteRequest(type="channel", id="ABCD")
        result = await toggle_favorite(request)

        assert result.favorites == []


class TestMigratePreferences:
    @pytest.mark.asyncio
    async def test_maps_frontend_payload_and_returns_migrated_true(self, test_db):
        request = MigratePreferencesRequest(
            favorites=[FavoriteRequest(type="contact", id="aa" * 32)],
            sort_order="alpha",
            last_message_times={"contact-aaaaaaaaaaaa": 123},
        )

        response = await migrate_preferences(request)

        assert response.migrated is True
        assert response.settings.preferences_migrated is True
        assert response.settings.sidebar_sort_order == "alpha"
        assert len(response.settings.favorites) == 1
        assert response.settings.favorites[0].type == "contact"
        assert response.settings.favorites[0].id == "aa" * 32
        assert response.settings.last_message_times == {"contact-aaaaaaaaaaaa": 123}

    @pytest.mark.asyncio
    async def test_returns_migrated_false_when_already_done(self, test_db):
        # First migration
        first_request = MigratePreferencesRequest(
            favorites=[FavoriteRequest(type="contact", id="bb" * 32)],
            sort_order="recent",
            last_message_times={},
        )
        await migrate_preferences(first_request)

        # Second attempt should be no-op
        second_request = MigratePreferencesRequest(
            favorites=[],
            sort_order="recent",
            last_message_times={},
        )
        response = await migrate_preferences(second_request)

        assert response.migrated is False
        assert response.settings.preferences_migrated is True
