import asyncio
import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models import AppSettings
from app.region_scope import normalize_region_scope
from app.repository import AppSettingsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettingsUpdate(BaseModel):
    max_radio_contacts: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Configured radio contact capacity used for maintenance thresholds and "
            "background refill behavior"
        ),
    )
    auto_decrypt_dm_on_advert: bool | None = Field(
        default=None,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    sidebar_sort_order: Literal["recent", "alpha"] | None = Field(
        default=None,
        description="Sidebar sort order: 'recent' or 'alpha'",
    )
    advert_interval: int | None = Field(
        default=None,
        ge=0,
        description="Periodic advertisement interval in seconds (0 = disabled, minimum 3600)",
    )
    flood_scope: str | None = Field(
        default=None,
        description="Outbound flood scope / region name (empty = disabled)",
    )
    blocked_keys: list[str] | None = Field(
        default=None,
        description="Public keys whose messages are hidden from the UI",
    )
    blocked_names: list[str] | None = Field(
        default=None,
        description="Display names whose messages are hidden from the UI",
    )


class BlockKeyRequest(BaseModel):
    key: str = Field(description="Public key to toggle block status")


class BlockNameRequest(BaseModel):
    name: str = Field(description="Display name to toggle block status")


class FavoriteRequest(BaseModel):
    type: Literal["channel", "contact"] = Field(description="'channel' or 'contact'")
    id: str = Field(description="Channel key or contact public key")


class MigratePreferencesRequest(BaseModel):
    favorites: list[FavoriteRequest] = Field(
        default_factory=list,
        description="List of favorites from localStorage",
    )
    sort_order: str = Field(
        default="recent",
        description="Sort order preference from localStorage",
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict,
        description="Map of conversation state keys to timestamps from localStorage",
    )


class MigratePreferencesResponse(BaseModel):
    migrated: bool = Field(description="Whether migration occurred (false if already migrated)")
    settings: AppSettings = Field(description="Current settings after migration attempt")


@router.get("", response_model=AppSettings)
async def get_settings() -> AppSettings:
    """Get current application settings."""
    return await AppSettingsRepository.get()


@router.patch("", response_model=AppSettings)
async def update_settings(update: AppSettingsUpdate) -> AppSettings:
    """Update application settings.

    Settings are persisted to the database and survive restarts.
    """
    kwargs = {}
    if update.max_radio_contacts is not None:
        logger.info("Updating max_radio_contacts to %d", update.max_radio_contacts)
        kwargs["max_radio_contacts"] = update.max_radio_contacts

    if update.auto_decrypt_dm_on_advert is not None:
        logger.info("Updating auto_decrypt_dm_on_advert to %s", update.auto_decrypt_dm_on_advert)
        kwargs["auto_decrypt_dm_on_advert"] = update.auto_decrypt_dm_on_advert

    if update.sidebar_sort_order is not None:
        logger.info("Updating sidebar_sort_order to %s", update.sidebar_sort_order)
        kwargs["sidebar_sort_order"] = update.sidebar_sort_order

    if update.advert_interval is not None:
        # Enforce minimum 1-hour interval; 0 means disabled
        interval = update.advert_interval
        if 0 < interval < 3600:
            interval = 3600
        logger.info("Updating advert_interval to %d", interval)
        kwargs["advert_interval"] = interval

    # Block lists
    if update.blocked_keys is not None:
        kwargs["blocked_keys"] = [k.lower() for k in update.blocked_keys]
    if update.blocked_names is not None:
        kwargs["blocked_names"] = update.blocked_names

    # Flood scope
    flood_scope_changed = False
    if update.flood_scope is not None:
        kwargs["flood_scope"] = normalize_region_scope(update.flood_scope)
        flood_scope_changed = True

    if kwargs:
        result = await AppSettingsRepository.update(**kwargs)

        # Apply flood scope to radio immediately if changed
        if flood_scope_changed:
            from app.services.radio_runtime import radio_runtime as radio_manager

            if radio_manager.is_connected:
                try:
                    scope = result.flood_scope
                    async with radio_manager.radio_operation("set_flood_scope") as mc:
                        await mc.set_flood_scope(scope if scope else "")
                        logger.info("Applied flood_scope=%r to radio", scope or "(disabled)")
                except Exception as e:
                    logger.warning("Failed to apply flood_scope to radio: %s", e)

        return result

    return await AppSettingsRepository.get()


@router.post("/favorites/toggle", response_model=AppSettings)
async def toggle_favorite(request: FavoriteRequest) -> AppSettings:
    """Toggle a conversation's favorite status."""
    settings = await AppSettingsRepository.get()
    is_favorited = any(f.type == request.type and f.id == request.id for f in settings.favorites)

    if is_favorited:
        logger.info("Removing favorite: %s %s", request.type, request.id[:12])
        result = await AppSettingsRepository.remove_favorite(request.type, request.id)
    else:
        logger.info("Adding favorite: %s %s", request.type, request.id[:12])
        result = await AppSettingsRepository.add_favorite(request.type, request.id)

    # When a contact is newly favorited, load just that contact to the radio
    # immediately so DM ACK support does not wait for the next maintenance cycle.
    if request.type == "contact" and not is_favorited:
        from app.radio_sync import ensure_contact_on_radio

        asyncio.create_task(ensure_contact_on_radio(request.id, force=True))

    return result


@router.post("/blocked-keys/toggle", response_model=AppSettings)
async def toggle_blocked_key(request: BlockKeyRequest) -> AppSettings:
    """Toggle a public key's blocked status."""
    logger.info("Toggling blocked key: %s", request.key[:12])
    return await AppSettingsRepository.toggle_blocked_key(request.key)


@router.post("/blocked-names/toggle", response_model=AppSettings)
async def toggle_blocked_name(request: BlockNameRequest) -> AppSettings:
    """Toggle a display name's blocked status."""
    logger.info("Toggling blocked name: %s", request.name)
    return await AppSettingsRepository.toggle_blocked_name(request.name)


@router.post("/migrate", response_model=MigratePreferencesResponse)
async def migrate_preferences(request: MigratePreferencesRequest) -> MigratePreferencesResponse:
    """Migrate all preferences from frontend localStorage to database.

    This is a one-time migration. If preferences have already been migrated,
    this endpoint will not overwrite them and will return migrated=false.

    Call this on frontend startup to ensure preferences are moved to the database.
    After successful migration, the frontend should clear localStorage preferences.

    Migrates:
    - favorites (remoteterm-favorites)
    - sort_order (remoteterm-sortOrder)
    - last_message_times (remoteterm-lastMessageTime)
    """
    # Convert to dict format for the repository method
    frontend_favorites = [{"type": f.type, "id": f.id} for f in request.favorites]

    settings, did_migrate = await AppSettingsRepository.migrate_preferences_from_frontend(
        favorites=frontend_favorites,
        sort_order=request.sort_order,
        last_message_times=request.last_message_times,
    )

    if did_migrate:
        logger.info(
            "Migrated preferences from frontend: %d favorites, sort_order=%s, %d message times",
            len(frontend_favorites),
            request.sort_order,
            len(request.last_message_times),
        )
    else:
        logger.debug("Preferences already migrated, skipping")

    return MigratePreferencesResponse(
        migrated=did_migrate,
        settings=settings,
    )
