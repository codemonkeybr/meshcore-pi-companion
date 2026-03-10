"""Tests for settings router endpoints and validation behavior."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import AppSettings
from app.repository import AppSettingsRepository
from app.routers.settings import (
    AppSettingsUpdate,
    FavoriteRequest,
    MigratePreferencesRequest,
    migrate_preferences,
    toggle_favorite,
    update_settings,
)


class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self, test_db):
        result = await update_settings(
            AppSettingsUpdate(
                max_radio_contacts=321,
                advert_interval=3600,
            )
        )

        assert result.max_radio_contacts == 321
        assert result.advert_interval == 3600

    @pytest.mark.asyncio
    async def test_advert_interval_below_minimum_is_clamped_to_one_hour(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=600))
        assert result.advert_interval == 3600

    @pytest.mark.asyncio
    async def test_advert_interval_zero_stays_disabled(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=0))
        assert result.advert_interval == 0

    @pytest.mark.asyncio
    async def test_advert_interval_above_minimum_is_preserved(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=86400))
        assert result.advert_interval == 86400

    @pytest.mark.asyncio
    async def test_empty_patch_returns_current_settings(self, test_db):
        result = await update_settings(AppSettingsUpdate())

        # Should return default settings without error
        assert isinstance(result, AppSettings)
        assert result.max_radio_contacts == 200  # default

    @pytest.mark.asyncio
    async def test_flood_scope_round_trip(self, test_db):
        """Flood scope should be saved and retrieved correctly."""
        result = await update_settings(AppSettingsUpdate(flood_scope="MyRegion"))
        assert result.flood_scope == "#MyRegion"

        fresh = await AppSettingsRepository.get()
        assert fresh.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_flood_scope_default_empty(self, test_db):
        """Fresh DB should have flood_scope as empty string."""
        settings = await AppSettingsRepository.get()
        assert settings.flood_scope == ""

    @pytest.mark.asyncio
    async def test_flood_scope_whitespace_stripped(self, test_db):
        """Flood scope should be stripped of whitespace."""
        result = await update_settings(AppSettingsUpdate(flood_scope="  MyRegion  "))
        assert result.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_flood_scope_existing_hash_is_not_doubled(self, test_db):
        """Existing leading hash should be preserved for backward compatibility."""
        result = await update_settings(AppSettingsUpdate(flood_scope="#MyRegion"))
        assert result.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_flood_scope_applies_to_radio(self, test_db):
        """When radio is connected, setting flood_scope calls set_flood_scope on radio."""
        mock_mc = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()

        mock_rm = AsyncMock()
        mock_rm.is_connected = True
        mock_rm.backend = mock_mc

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_radio_op(name):
            yield mock_mc

        mock_rm.radio_operation = mock_radio_op

        with patch("app.radio.radio_manager", mock_rm):
            await update_settings(AppSettingsUpdate(flood_scope="TestRegion"))

        mock_mc.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_flood_scope_empty_resets_radio(self, test_db):
        """Setting flood_scope to empty calls set_flood_scope("") on radio."""
        # First set a non-empty scope
        await update_settings(AppSettingsUpdate(flood_scope="#TestRegion"))

        mock_mc = AsyncMock()
        mock_mc.set_flood_scope = AsyncMock()

        mock_rm = AsyncMock()
        mock_rm.is_connected = True
        mock_rm.backend = mock_mc

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_radio_op(name):
            yield mock_mc

        mock_rm.radio_operation = mock_radio_op

        with patch("app.radio.radio_manager", mock_rm):
            await update_settings(AppSettingsUpdate(flood_scope=""))

        mock_mc.set_flood_scope.assert_awaited_once_with("")


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
