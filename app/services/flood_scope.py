"""Firmware-compatible flood-scope command helpers."""

import logging
from typing import Any

from meshcore import EventType
from meshcore.packets import CommandType

from app.region_scope import normalize_region_scope

logger = logging.getLogger(__name__)

SET_FLOOD_SCOPE_MODE_UNSCOPED = 1
FORCE_UNSCOPED_FRAME = bytes([CommandType.SET_FLOOD_SCOPE.value, SET_FLOOD_SCOPE_MODE_UNSCOPED])

# CMD_SET_FLOOD_SCOPE_KEY mode 1 (the firmware ``send_unscoped`` flag) is companion
# firmware ver 12+. On older firmware the mode-1 frame is rejected, so we fall back
# to resetting the scope override (mode 0, zero key), which makes the radio use its
# configured default scope. True "unscoped while a default scope is set" is not
# achievable pre-v12.
FIRMWARE_VER_UNSCOPED_MODE = 12


def firmware_supports_unscoped_mode(fw_ver: int | None) -> bool:
    """Whether the radio's protocol version supports the mode-1 unscoped command."""
    return fw_ver is not None and fw_ver >= FIRMWARE_VER_UNSCOPED_MODE


async def set_radio_flood_scope(mc, scope: str | None, *, fw_ver: int | None = None) -> Any:
    """Apply the standing radio flood-scope state.

    A non-empty scope is delegated to meshcore_py's mode-0 ``set_flood_scope``. An
    empty scope means explicit unscoped/plain flood:

    - firmware >= 12: use the dedicated mode-1 command (``force_radio_unscoped``).
    - older / unknown firmware: no dedicated unscoped command exists, so reset the
      scope override (mode 0, zero key); the radio falls back to its configured
      default scope. ``fw_ver=None`` (version unknown) is treated conservatively as
      unsupported so we never emit a frame the radio might reject.
    """
    normalized_scope = normalize_region_scope(scope)
    if normalized_scope:
        return await mc.commands.set_flood_scope(normalized_scope)

    if firmware_supports_unscoped_mode(fw_ver):
        return await force_radio_unscoped(mc)

    logger.debug(
        "Radio fw_ver=%s < %d: no dedicated unscoped command; resetting scope override "
        "(radio falls back to its configured default scope)",
        fw_ver,
        FIRMWARE_VER_UNSCOPED_MODE,
    )
    return await mc.commands.set_flood_scope("")


async def force_radio_unscoped(mc) -> Any:
    """Tell the radio to send following flood packets unscoped until mode 0."""

    return await mc.commands.send(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
