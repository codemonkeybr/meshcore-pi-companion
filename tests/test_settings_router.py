"""Tests for settings router endpoints and validation behavior."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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

    @pytest.mark.asyncio
    async def test_mqtt_fields_round_trip(self, test_db):
        """MQTT settings should be saved and retrieved correctly."""
        mock_publisher = type("MockPublisher", (), {"restart": AsyncMock()})()
        with patch("app.mqtt.mqtt_publisher", mock_publisher):
            result = await update_settings(
                AppSettingsUpdate(
                    mqtt_broker_host="broker.test",
                    mqtt_broker_port=8883,
                    mqtt_username="user",
                    mqtt_password="pass",
                    mqtt_use_tls=True,
                    mqtt_tls_insecure=True,
                    mqtt_topic_prefix="custom",
                    mqtt_publish_messages=True,
                    mqtt_publish_raw_packets=True,
                )
            )

        assert result.mqtt_broker_host == "broker.test"
        assert result.mqtt_broker_port == 8883
        assert result.mqtt_username == "user"
        assert result.mqtt_password == "pass"
        assert result.mqtt_use_tls is True
        assert result.mqtt_tls_insecure is True
        assert result.mqtt_topic_prefix == "custom"
        assert result.mqtt_publish_messages is True
        assert result.mqtt_publish_raw_packets is True

        # Verify persistence
        fresh = await AppSettingsRepository.get()
        assert fresh.mqtt_broker_host == "broker.test"
        assert fresh.mqtt_use_tls is True

    @pytest.mark.asyncio
    async def test_mqtt_defaults_on_fresh_db(self, test_db):
        """MQTT fields should have correct defaults on a fresh database."""
        settings = await AppSettingsRepository.get()

        assert settings.mqtt_broker_host == ""
        assert settings.mqtt_broker_port == 1883
        assert settings.mqtt_username == ""
        assert settings.mqtt_password == ""
        assert settings.mqtt_use_tls is False
        assert settings.mqtt_tls_insecure is False
        assert settings.mqtt_topic_prefix == "meshcore"
        assert settings.mqtt_publish_messages is False
        assert settings.mqtt_publish_raw_packets is False

    @pytest.mark.asyncio
    async def test_community_mqtt_fields_round_trip(self, test_db):
        """Community MQTT settings should be saved and retrieved correctly."""
        mock_community = type("MockCommunity", (), {"restart": AsyncMock()})()
        with patch("app.community_mqtt.community_publisher", mock_community):
            result = await update_settings(
                AppSettingsUpdate(
                    community_mqtt_enabled=True,
                    community_mqtt_iata="DEN",
                    community_mqtt_broker_host="custom-broker.example.com",
                    community_mqtt_broker_port=8883,
                    community_mqtt_email="test@example.com",
                )
            )

        assert result.community_mqtt_enabled is True
        assert result.community_mqtt_iata == "DEN"
        assert result.community_mqtt_broker_host == "custom-broker.example.com"
        assert result.community_mqtt_broker_port == 8883
        assert result.community_mqtt_email == "test@example.com"

        # Verify persistence
        fresh = await AppSettingsRepository.get()
        assert fresh.community_mqtt_enabled is True
        assert fresh.community_mqtt_iata == "DEN"
        assert fresh.community_mqtt_broker_host == "custom-broker.example.com"
        assert fresh.community_mqtt_broker_port == 8883
        assert fresh.community_mqtt_email == "test@example.com"

        # Verify restart was called
        mock_community.restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_community_mqtt_iata_validation_rejects_invalid(self, test_db):
        """Invalid IATA codes should be rejected."""
        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(community_mqtt_iata="A"))
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(community_mqtt_iata="ABCDE"))
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(community_mqtt_iata="12"))
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(community_mqtt_iata="ABCD"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_community_mqtt_enable_requires_iata(self, test_db):
        """Enabling community MQTT without a valid IATA code should be rejected."""
        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(community_mqtt_enabled=True))
        assert exc.value.status_code == 400
        assert "IATA" in exc.value.detail

    @pytest.mark.asyncio
    async def test_community_mqtt_iata_uppercased(self, test_db):
        """IATA codes should be uppercased."""
        mock_community = type("MockCommunity", (), {"restart": AsyncMock()})()
        with patch("app.community_mqtt.community_publisher", mock_community):
            result = await update_settings(AppSettingsUpdate(community_mqtt_iata="den"))
        assert result.community_mqtt_iata == "DEN"

    @pytest.mark.asyncio
    async def test_community_mqtt_defaults_on_fresh_db(self, test_db):
        """Community MQTT fields should have correct defaults on a fresh database."""
        settings = await AppSettingsRepository.get()
        assert settings.community_mqtt_enabled is False
        assert settings.community_mqtt_iata == ""
        assert settings.community_mqtt_email == ""


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
