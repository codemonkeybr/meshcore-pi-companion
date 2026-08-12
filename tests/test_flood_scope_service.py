from unittest.mock import AsyncMock, MagicMock

import pytest
from meshcore import EventType

from app.services.flood_scope import (
    FIRMWARE_VER_UNSCOPED_MODE,
    FORCE_UNSCOPED_FRAME,
    firmware_supports_unscoped_mode,
    set_radio_flood_scope,
)

# A protocol version that supports the mode-1 unscoped command (>= 12).
FW_SUPPORTS_UNSCOPED = FIRMWARE_VER_UNSCOPED_MODE
# A protocol version that predates the mode-1 command.
FW_NO_UNSCOPED = FIRMWARE_VER_UNSCOPED_MODE - 1


@pytest.mark.asyncio
async def test_set_radio_flood_scope_uses_meshcore_scope_for_regions():
    mc = MagicMock()
    mc.commands.set_flood_scope = AsyncMock(return_value="ok")
    mc.commands.send = AsyncMock()

    # Region path is version-independent.
    result = await set_radio_flood_scope(mc, "Esperance", fw_ver=FW_NO_UNSCOPED)

    assert result == "ok"
    mc.commands.set_flood_scope.assert_awaited_once_with("#Esperance")
    mc.commands.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_radio_flood_scope_empty_uses_firmware_unscoped_mode_on_v12():
    mc = MagicMock()
    mc.commands.set_flood_scope = AsyncMock()
    mc.commands.send = AsyncMock(return_value="ok")

    result = await set_radio_flood_scope(mc, "", fw_ver=FW_SUPPORTS_UNSCOPED)

    assert result == "ok"
    mc.commands.send.assert_awaited_once_with(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
    mc.commands.set_flood_scope.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "   ", "0", "*"])
async def test_set_radio_flood_scope_unscoped_sentinels_use_firmware_mode_on_v12(scope):
    mc = MagicMock()
    mc.commands.set_flood_scope = AsyncMock()
    mc.commands.send = AsyncMock(return_value="ok")

    result = await set_radio_flood_scope(mc, scope, fw_ver=FW_SUPPORTS_UNSCOPED)

    assert result == "ok"
    mc.commands.send.assert_awaited_once_with(FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR])
    mc.commands.set_flood_scope.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fw_ver", [None, FW_NO_UNSCOPED])
async def test_set_radio_flood_scope_unscoped_falls_back_on_old_or_unknown_firmware(fw_ver):
    """Pre-v12 (or unknown) firmware has no mode-1 command; reset scope via mode 0."""
    mc = MagicMock()
    mc.commands.set_flood_scope = AsyncMock(return_value="ok")
    mc.commands.send = AsyncMock()

    result = await set_radio_flood_scope(mc, "", fw_ver=fw_ver)

    assert result == "ok"
    mc.commands.set_flood_scope.assert_awaited_once_with("")
    mc.commands.send.assert_not_awaited()


def test_firmware_supports_unscoped_mode():
    assert firmware_supports_unscoped_mode(FIRMWARE_VER_UNSCOPED_MODE) is True
    assert firmware_supports_unscoped_mode(FIRMWARE_VER_UNSCOPED_MODE + 1) is True
    assert firmware_supports_unscoped_mode(FIRMWARE_VER_UNSCOPED_MODE - 1) is False
    assert firmware_supports_unscoped_mode(None) is False
