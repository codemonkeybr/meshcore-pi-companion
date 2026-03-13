import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import require_connected
from app.radio_sync import send_advertisement as do_send_advertisement
from app.radio_sync import sync_radio_time
from app.services.radio_commands import (
    KeystoreRefreshError,
    PathHashModeUnsupportedError,
    RadioCommandRejectedError,
    apply_radio_config_update,
    import_private_key_and_refresh_keystore,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_health

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio", tags=["radio"])

AdvertLocationSource = Literal["off", "current"]


async def _prepare_connected(*, broadcast_on_success: bool) -> bool:
    return await radio_manager.prepare_connected(broadcast_on_success=broadcast_on_success)


async def _reconnect_and_prepare(*, broadcast_on_success: bool) -> bool:
    return await radio_manager.reconnect_and_prepare(
        broadcast_on_success=broadcast_on_success,
    )


class RadioSettings(BaseModel):
    freq: float = Field(description="Frequency in MHz")
    bw: float = Field(description="Bandwidth in kHz")
    sf: int = Field(description="Spreading factor (7-12)")
    cr: int = Field(description="Coding rate (1-4)")


class RadioConfigResponse(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str
    lat: float
    lon: float
    tx_power: int = Field(description="Transmit power in dBm")
    max_tx_power: int = Field(description="Maximum transmit power in dBm")
    radio: RadioSettings
    path_hash_mode: int = Field(
        default=0, description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte)"
    )
    path_hash_mode_supported: bool = Field(
        default=False, description="Whether firmware supports path hash mode setting"
    )
    advert_location_source: AdvertLocationSource = Field(
        default="current",
        description="Whether adverts include the node's current location state",
    )


class RadioConfigUpdate(BaseModel):
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    tx_power: int | None = Field(default=None, description="Transmit power in dBm")
    radio: RadioSettings | None = None
    path_hash_mode: int | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte)",
    )
    advert_location_source: AdvertLocationSource | None = Field(
        default=None,
        description="Whether adverts include the node's current location state",
    )


class PrivateKeyUpdate(BaseModel):
    private_key: str = Field(description="Private key as hex string")


@router.get("/config", response_model=RadioConfigResponse)
async def get_radio_config() -> RadioConfigResponse:
    """Get the current radio configuration."""
    mc = require_connected()

    info = mc.self_info
    if not info:
        raise HTTPException(status_code=503, detail="Radio info not available")

    adv_loc_policy = info.get("adv_loc_policy", 1)
    advert_location_source: AdvertLocationSource = "off" if adv_loc_policy == 0 else "current"

    return RadioConfigResponse(
        public_key=info.get("public_key", ""),
        name=info.get("name", ""),
        lat=info.get("adv_lat", 0.0),
        lon=info.get("adv_lon", 0.0),
        tx_power=info.get("tx_power", 0),
        max_tx_power=info.get("max_tx_power", 0),
        radio=RadioSettings(
            freq=info.get("radio_freq", 0.0),
            bw=info.get("radio_bw", 0.0),
            sf=info.get("radio_sf", 0),
            cr=info.get("radio_cr", 0),
        ),
        path_hash_mode=radio_manager.path_hash_mode,
        path_hash_mode_supported=radio_manager.path_hash_mode_supported,
        advert_location_source=advert_location_source,
    )


@router.patch("/config", response_model=RadioConfigResponse)
def _meshcore_from_backend(be):
    """Return the underlying MeshCore for command services; ClientBackend has _mc, SpiBackend is passed through."""
    return getattr(be, "_mc", be)


async def update_radio_config(update: RadioConfigUpdate) -> RadioConfigResponse:
    """Update radio configuration. Only provided fields will be updated."""
    require_connected()

    async with radio_manager.radio_operation("update_radio_config") as be:
        mc = _meshcore_from_backend(be)
        try:
            await apply_radio_config_update(
                mc,
                update,
                path_hash_mode_supported=radio_manager.path_hash_mode_supported,
                set_path_hash_mode=lambda mode: setattr(radio_manager, "path_hash_mode", mode),
                sync_radio_time_fn=lambda _: sync_radio_time(be),
            )
        except PathHashModeUnsupportedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RadioCommandRejectedError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return await get_radio_config()


@router.put("/private-key")
async def set_private_key(update: PrivateKeyUpdate) -> dict:
    """Set the radio's private key. This is write-only."""
    require_connected()

    try:
        key_bytes = bytes.fromhex(update.private_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string for private key") from None

    logger.info("Importing private key")
    async with radio_manager.radio_operation("import_private_key") as be:
        from app.keystore import export_and_store_private_key

        mc = _meshcore_from_backend(be)
        try:
            await import_private_key_and_refresh_keystore(
                mc,
                key_bytes,
                export_and_store_private_key_fn=export_and_store_private_key,
            )
        except (RadioCommandRejectedError, KeystoreRefreshError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "ok"}


# Max time for advertise: wait for radio lock + build packet + TX complete
ADVERTISE_TIMEOUT_SECONDS = 25


@router.post("/advertise")
async def send_advertisement() -> dict:
    """Send a flood advertisement to announce presence on the mesh.

    Manual advertisement requests always send immediately, updating the
    last_advert_time which affects when the next periodic/startup advert
    can occur.

    Returns:
        status: "ok" if sent successfully
    """
    require_connected()

    logger.info("Sending flood advertisement")
    try:
        async def _do():
            async with radio_manager.radio_operation("manual_advertisement") as mc:
                return await do_send_advertisement(mc, force=True)

        success = await asyncio.wait_for(_do(), timeout=ADVERTISE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Advertisement timed out (radio busy or TX did not complete)")
        raise HTTPException(
            status_code=503,
            detail="Advertisement timed out. Radio may be busy or transmission did not complete; try again in a few seconds.",
        ) from None

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send advertisement")

    return {"status": "ok"}


async def _attempt_reconnect() -> dict:
    """Shared reconnection logic for reboot and reconnect endpoints."""
    radio_manager.resume_connection()

    if radio_manager.is_reconnecting:
        return {
            "status": "pending",
            "message": "Reconnection already in progress",
            "connected": False,
        }

    try:
        success = await _reconnect_and_prepare(broadcast_on_success=True)
    except Exception as e:
        logger.exception("Post-connect setup failed after reconnect")
        raise HTTPException(
            status_code=503,
            detail=f"Radio connected but setup failed: {e}",
        ) from e

    if not success:
        raise HTTPException(
            status_code=503, detail="Failed to reconnect. Check radio connection and power."
        )

    return {"status": "ok", "message": "Reconnected successfully", "connected": True}


@router.post("/disconnect")
async def disconnect_radio() -> dict:
    """Disconnect from the radio and pause automatic reconnect attempts."""
    logger.info("Manual radio disconnect requested")
    await radio_manager.pause_connection()
    broadcast_health(False, radio_manager.connection_info)
    return {
        "status": "ok",
        "message": "Disconnected. Automatic reconnect is paused.",
        "connected": False,
        "paused": True,
    }


@router.post("/reboot")
async def reboot_radio() -> dict:
    """Reboot the radio, or reconnect if not currently connected.

    If connected: sends reboot command, connection will temporarily drop and auto-reconnect.
    If not connected: attempts to reconnect (same as /reconnect endpoint).

    Note: On SPI backend, the driver may not release GPIO until process exit. If reconnect
    fails with \"GPIO already in use\", restart the app manually to clear the pin.
    """
    if radio_manager.is_connected:
        logger.info("Rebooting radio")
        # Prevent connection monitor from reconnecting during cooldown so GPIO can be released
        radio_manager.set_suppress_reconnect_until(20)
        async with radio_manager.radio_operation("reboot_radio") as mc:
            await mc.reboot()
        return {
            "status": "ok",
            "message": "Reboot command sent. Radio will reconnect automatically.",
        }

    logger.info("Radio not connected, attempting reconnect")
    return await _attempt_reconnect()


@router.post("/reconnect")
async def reconnect_radio() -> dict:
    """Attempt to reconnect to the radio.

    This will try to re-establish connection to the radio, with auto-detection
    if no specific port is configured. Useful when the radio has been disconnected
    or power-cycled.
    """
    if radio_manager.is_connected:
        if radio_manager.is_setup_complete:
            return {"status": "ok", "message": "Already connected", "connected": True}

        logger.info("Radio connected but setup incomplete, retrying setup")
        try:
            if not await _prepare_connected(broadcast_on_success=True):
                raise HTTPException(status_code=503, detail="Radio connection is paused")
            return {"status": "ok", "message": "Setup completed", "connected": True}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Post-connect setup failed")
            raise HTTPException(
                status_code=503,
                detail=f"Radio connected but setup failed: {e}",
            ) from e

    logger.info("Manual reconnect requested")
    return await _attempt_reconnect()
