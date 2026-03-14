import logging
from collections.abc import Awaitable, Callable
from typing import Any

from meshcore import EventType

logger = logging.getLogger(__name__)


class RadioCommandServiceError(RuntimeError):
    """Base error for reusable radio command workflows."""


class PathHashModeUnsupportedError(RadioCommandServiceError):
    """Raised when firmware does not support path hash mode updates."""


class RadioCommandRejectedError(RadioCommandServiceError):
    """Raised when the radio reports an error for a command."""


class KeystoreRefreshError(RadioCommandServiceError):
    """Raised when server-side keystore refresh fails after import."""


async def apply_radio_config_update(
    mc,
    update,
    *,
    path_hash_mode_supported: bool,
    set_path_hash_mode: Callable[[int], None],
    sync_radio_time_fn: Callable[[Any], Awaitable[Any]],
) -> None:
    """Apply a validated radio-config update to the connected radio."""
    if update.advert_location_source is not None:
        advert_loc_policy = 0 if update.advert_location_source == "off" else 1
        logger.info(
            "Setting advert location policy to %s",
            update.advert_location_source,
        )
        result = await mc.commands.set_advert_loc_policy(advert_loc_policy)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set advert location policy: {result.payload}"
            )

    if update.name is not None:
        logger.info("Setting radio name to %s", update.name)
        await mc.commands.set_name(update.name)

    if update.lat is not None or update.lon is not None:
        current_info = mc.self_info
        lat = update.lat if update.lat is not None else current_info.get("adv_lat", 0.0)
        lon = update.lon if update.lon is not None else current_info.get("adv_lon", 0.0)
        logger.info("Setting radio coordinates to %f, %f", lat, lon)
        await mc.commands.set_coords(lat=lat, lon=lon)

    if update.tx_power is not None:
        logger.info("Setting TX power to %d dBm", update.tx_power)
        await mc.commands.set_tx_power(val=update.tx_power)

    if update.radio is not None:
        logger.info(
            "Setting radio params: freq=%f MHz, bw=%f kHz, sf=%d, cr=%d",
            update.radio.freq,
            update.radio.bw,
            update.radio.sf,
            update.radio.cr,
        )
        await mc.commands.set_radio(
            freq=update.radio.freq,
            bw=update.radio.bw,
            sf=update.radio.sf,
            cr=update.radio.cr,
        )

    if update.path_hash_mode is not None:
        if not path_hash_mode_supported:
            raise PathHashModeUnsupportedError("Firmware does not support path hash mode setting")

        logger.info("Setting path hash mode to %d", update.path_hash_mode)
        result = await mc.commands.set_path_hash_mode(update.path_hash_mode)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to set path hash mode: {result.payload}")
        set_path_hash_mode(update.path_hash_mode)

    await sync_radio_time_fn(mc)

    # Commands like set_name() write to flash but don't update cached self_info.
    # send_appstart() forces a fresh SELF_INFO so the response reflects changes.
    await mc.commands.send_appstart()


async def import_private_key_and_refresh_keystore(
    mc,
    key_bytes: bytes,
    *,
    export_and_store_private_key_fn: Callable[[Any], Awaitable[bool]],
) -> None:
    """Import a private key and refresh the in-memory keystore immediately."""
    result = await mc.commands.import_private_key(key_bytes)
    if result.type == EventType.ERROR:
        raise RadioCommandRejectedError(f"Failed to import private key: {result.payload}")

    keystore_refreshed = await export_and_store_private_key_fn(mc)
    if not keystore_refreshed:
        logger.warning("Keystore refresh failed after import, retrying once")
        keystore_refreshed = await export_and_store_private_key_fn(mc)

    if not keystore_refreshed:
        raise KeystoreRefreshError(
            "Private key imported on radio, but server-side keystore refresh failed. "
            "Reconnect to apply the new key for DM decryption."
        )
