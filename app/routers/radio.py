import asyncio
import logging
import sys

from fastapi import APIRouter, HTTPException
from meshcore import EventType
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies import require_connected
from app.radio import radio_manager
from app.radio_sync import send_advertisement as do_send_advertisement
from app.radio_sync import sync_radio_time

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio", tags=["radio"])


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


class PrivateKeyUpdate(BaseModel):
    private_key: str = Field(description="Private key as hex string")


@router.get("/config", response_model=RadioConfigResponse)
async def get_radio_config() -> RadioConfigResponse:
    """Get the current radio configuration."""
    mc = require_connected()

    info = mc.self_info
    if not info:
        raise HTTPException(status_code=503, detail="Radio info not available")

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
    )


@router.patch("/config", response_model=RadioConfigResponse)
async def update_radio_config(update: RadioConfigUpdate) -> RadioConfigResponse:
    """Update radio configuration. Only provided fields will be updated."""
    require_connected()

    async with radio_manager.radio_operation("update_radio_config") as mc:
        if update.name is not None:
            logger.info("Setting radio name to %s", update.name)
            await mc.set_name(update.name)

        if update.lat is not None or update.lon is not None:
            current_info = mc.self_info or {}
            lat = update.lat if update.lat is not None else current_info.get("adv_lat", 0.0)
            lon = update.lon if update.lon is not None else current_info.get("adv_lon", 0.0)
            logger.info("Setting radio coordinates to %f, %f", lat, lon)
            await mc.set_coords(lat=lat, lon=lon)

        if update.tx_power is not None:
            logger.info("Setting TX power to %d dBm", update.tx_power)
            await mc.set_tx_power(val=update.tx_power)

        if update.radio is not None:
            logger.info(
                "Setting radio params: freq=%f MHz, bw=%f kHz, sf=%d, cr=%d",
                update.radio.freq,
                update.radio.bw,
                update.radio.sf,
                update.radio.cr,
            )
            await mc.set_radio(
                freq=update.radio.freq,
                bw=update.radio.bw,
                sf=update.radio.sf,
                cr=update.radio.cr,
            )

        if update.path_hash_mode is not None:
            if not radio_manager.path_hash_mode_supported:
                raise HTTPException(
                    status_code=400, detail="Firmware does not support path hash mode setting"
                )
            logger.info("Setting path hash mode to %d", update.path_hash_mode)
            result = await mc.set_path_hash_mode(update.path_hash_mode)
            if result is not None and result.type == EventType.ERROR:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to set path hash mode: {result.payload}",
                )
            radio_manager.path_hash_mode = update.path_hash_mode

        # Sync time with system clock
        await sync_radio_time(mc)

        # Re-fetch self_info so the response reflects the changes we just made.
        # Commands like set_name() write to flash but don't update the cached
        # self_info — send_appstart() triggers a fresh SELF_INFO from the radio.
        await mc.send_appstart()

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
    async with radio_manager.radio_operation("import_private_key") as mc:
        result = await mc.import_private_key(key_bytes)

        if result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500, detail=f"Failed to import private key: {result.payload}"
            )

        # Re-export from radio so the server-side keystore uses the new key
        # for DM decryption immediately, rather than waiting for reconnect.
        from app.keystore import export_and_store_private_key

        keystore_refreshed = await export_and_store_private_key(mc)
        if not keystore_refreshed:
            logger.warning("Keystore refresh failed after import, retrying once")
            keystore_refreshed = await export_and_store_private_key(mc)

    if not keystore_refreshed:
        raise HTTPException(
            status_code=500,
            detail=(
                "Private key imported on radio, but server-side keystore "
                "refresh failed. Reconnect to apply the new key for DM decryption."
            ),
        )

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
    if radio_manager.is_reconnecting:
        return {
            "status": "pending",
            "message": "Reconnection already in progress",
            "connected": False,
        }

    success = await radio_manager.reconnect()
    if not success:
        raise HTTPException(
            status_code=503, detail="Failed to reconnect. Check radio connection and power."
        )

    try:
        await radio_manager.post_connect_setup()
    except Exception as e:
        logger.exception("Post-connect setup failed after reconnect")
        raise HTTPException(
            status_code=503,
            detail=f"Radio connected but setup failed: {e}",
        ) from e

    return {"status": "ok", "message": "Reconnected successfully", "connected": True}


async def _exit_after_reboot() -> None:
    """Exit process after a short delay so HTTP response is sent; supervisor restarts us."""
    await asyncio.sleep(2)
    logger.info("Exiting after SPI radio reboot so GPIO is released; supervisor should restart.")
    sys.exit(0)


@router.post("/reboot")
async def reboot_radio() -> dict:
    """Reboot the radio, or reconnect if not currently connected.

    If connected: sends reboot command, connection will temporarily drop and auto-reconnect.
    If not connected: attempts to reconnect (same as /reconnect endpoint).
    """
    if radio_manager.is_connected:
        logger.info("Rebooting radio")
        # Prevent connection monitor from reconnecting during cooldown so GPIO is released
        radio_manager.set_suppress_reconnect_until(20)
        async with radio_manager.radio_operation("reboot_radio") as mc:
            await mc.reboot()
        # SPI: GPIO is not released by the driver until process exit. Schedule exit so
        # the response is sent first; a process manager (systemd, Docker) should restart us.
        if settings.connection_type == "spi":
            asyncio.create_task(_exit_after_reboot())
            return {
                "status": "ok",
                "message": "Reboot command sent. App will exit and restart to release GPIO.",
            }
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
            await radio_manager.post_connect_setup()
            return {"status": "ok", "message": "Setup completed", "connected": True}
        except Exception as e:
            logger.exception("Post-connect setup failed")
            raise HTTPException(
                status_code=503,
                detail=f"Radio connected but setup failed: {e}",
            ) from e

    logger.info("Manual reconnect requested")
    return await _attempt_reconnect()
