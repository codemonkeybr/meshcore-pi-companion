import hashlib
import logging
import os
import platform
import struct
import sys
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from meshcore import EventType
from pydantic import BaseModel, Field

from app.config import get_recent_log_lines, settings
from app.models import AppSettings
from app.radio_sync import get_contacts_selected_for_radio_sync, get_radio_channel_limit
from app.repository import AppSettingsRepository, MessageRepository, StatisticsRepository
from app.routers.health import FanoutStatusResponse, build_health_data
from app.services.radio_runtime import radio_runtime
from app.version_info import get_app_build_info, git_output

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debug"])

LOG_COPY_BOUNDARY_MESSAGE = "STOP COPYING HERE IF YOU DO NOT WANT TO INCLUDE LOGS BELOW"
LOG_COPY_BOUNDARY_LINE = "-" * 64
LOG_COPY_BOUNDARY_PREFIX = [
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_MESSAGE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
    LOG_COPY_BOUNDARY_LINE,
]


class DebugSystemInfo(BaseModel):
    os: str
    arch: str
    arch_bits: int
    total_ram_mb: int


class DebugApplicationInfo(BaseModel):
    version: str
    version_source: str
    commit_hash: str | None = None
    commit_source: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    python_version: str


class DebugRuntimeInfo(BaseModel):
    connection_info: str | None = None
    connection_desired: bool
    setup_in_progress: bool
    setup_complete: bool
    channels_with_incoming_messages: int
    path_hash_mode_supported: bool
    channel_slot_reuse_enabled: bool
    channel_send_cache_capacity: int


class DebugContactAudit(BaseModel):
    expected_and_found: int
    expected_but_not_found: list[str]
    found_but_not_expected: list[str]


class DebugChannelSlotMismatch(BaseModel):
    slot_number: int
    expected_sha256_of_room_key: str | None = None
    actual_sha256_of_room_key: str | None = None


class DebugChannelAudit(BaseModel):
    matched_slots: int
    wrong_slots: list[DebugChannelSlotMismatch]


class DebugRadioProbe(BaseModel):
    performed: bool
    errors: list[str] = Field(default_factory=list)
    self_info: dict[str, Any] | None = None
    device_info: dict[str, Any] | None = None
    stats_core: dict[str, Any] | None = None
    stats_radio: dict[str, Any] | None = None
    contacts: DebugContactAudit | None = None
    channels: DebugChannelAudit | None = None


class DebugDatabaseInfo(BaseModel):
    total_dms: int
    total_channel_messages: int
    total_outgoing: int


class DebugHealthSummary(BaseModel):
    radio_state: str
    database_size_mb: float
    oldest_undecrypted_timestamp: int | None
    fanouts_with_errors: dict[str, FanoutStatusResponse] = Field(default_factory=dict)
    bots_disabled_source: Literal["env", "until_restart"] | None = None
    basic_auth_enabled: bool = False


class DebugEnvironment(BaseModel):
    connection_type: str
    serial_port: str
    serial_baudrate: int
    tcp_host: str
    tcp_port: int
    ble_address: str
    log_level: str
    database_path: str
    disable_bots: bool
    enable_message_poll_fallback: bool
    force_channel_slot_reconfigure: bool
    load_with_autoevict: bool


class DebugAppSettings(BaseModel):
    max_radio_contacts: int
    auto_decrypt_dm_on_advert: bool
    advert_interval: int
    flood_scope: str
    blocked_keys_count: int
    blocked_names_count: int


class DebugSnapshotResponse(BaseModel):
    captured_at: str
    system: DebugSystemInfo
    application: DebugApplicationInfo
    environment: DebugEnvironment
    health: DebugHealthSummary
    settings: DebugAppSettings
    runtime: DebugRuntimeInfo
    database: DebugDatabaseInfo
    radio_probe: DebugRadioProbe
    logs: list[str]


def _build_system_info() -> DebugSystemInfo:
    try:
        # os.sysconf is available on Linux/macOS
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        total_ram_mb = (page_size * page_count) // (1024 * 1024)
    except (AttributeError, ValueError, OSError):
        total_ram_mb = 0

    return DebugSystemInfo(
        os=f"{platform.system()} {platform.release()}",
        arch=platform.machine(),
        arch_bits=struct.calcsize("P") * 8,
        total_ram_mb=total_ram_mb,
    )


def _build_application_info() -> DebugApplicationInfo:
    build_info = get_app_build_info()
    dirty_output = git_output("status", "--porcelain")
    return DebugApplicationInfo(
        version=build_info.version,
        version_source=build_info.version_source,
        commit_hash=build_info.commit_hash,
        commit_source=build_info.commit_source,
        git_branch=git_output("rev-parse", "--abbrev-ref", "HEAD"),
        git_dirty=(dirty_output is not None and dirty_output != ""),
        python_version=sys.version.split()[0],
    )


def _event_type_name(event: Any) -> str:
    event_type = getattr(event, "type", None)
    return getattr(event_type, "name", str(event_type))


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_channel_secret(payload: dict[str, Any]) -> bytes:
    secret = payload.get("channel_secret", b"")
    if isinstance(secret, bytes):
        return secret
    return bytes(secret)


def _is_empty_channel_payload(payload: dict[str, Any]) -> bool:
    name = payload.get("channel_name", "")
    return not name or name == "\x00" * len(name)


def _observed_channel_key(event: Any) -> str | None:
    if getattr(event, "type", None) != EventType.CHANNEL_INFO:
        return None

    payload = event.payload or {}
    if _is_empty_channel_payload(payload):
        return None

    return _normalize_channel_secret(payload).hex().upper()


def _coerce_live_max_channels(device_info: dict[str, Any] | None) -> int | None:
    if not device_info or "max_channels" not in device_info:
        return None
    try:
        return int(device_info["max_channels"])
    except (TypeError, ValueError):
        return None


def _build_environment() -> DebugEnvironment:
    return DebugEnvironment(
        connection_type=settings.connection_type,
        serial_port=settings.serial_port,
        serial_baudrate=settings.serial_baudrate,
        tcp_host=settings.tcp_host,
        tcp_port=settings.tcp_port,
        ble_address=settings.ble_address,
        log_level=settings.log_level,
        database_path=settings.database_path,
        disable_bots=settings.disable_bots,
        enable_message_poll_fallback=settings.enable_message_poll_fallback,
        force_channel_slot_reconfigure=settings.force_channel_slot_reconfigure,
        load_with_autoevict=settings.load_with_autoevict,
    )


def _build_debug_app_settings(app_settings: AppSettings) -> DebugAppSettings:
    return DebugAppSettings(
        max_radio_contacts=app_settings.max_radio_contacts,
        auto_decrypt_dm_on_advert=app_settings.auto_decrypt_dm_on_advert,
        advert_interval=app_settings.advert_interval,
        flood_scope=app_settings.flood_scope,
        blocked_keys_count=len(app_settings.blocked_keys),
        blocked_names_count=len(app_settings.blocked_names),
    )


def _derive_debug_radio_state(
    *,
    radio_connected: bool,
    connection_desired: bool,
    setup_in_progress: bool,
    setup_complete: bool,
    is_reconnecting: bool,
) -> str:
    if not connection_desired:
        return "paused"
    if radio_connected and (setup_in_progress or not setup_complete):
        return "initializing"
    if radio_connected:
        return "connected"
    if is_reconnecting:
        return "connecting"
    return "disconnected"


def _build_debug_health_summary(
    health_data: dict[str, Any], *, radio_state: str
) -> DebugHealthSummary:
    def _fanout_last_error(status: Any) -> str | None:
        if isinstance(status, dict):
            value = status.get("last_error")
        else:
            value = getattr(status, "last_error", None)
        return value if isinstance(value, str) and value else None

    fanouts_with_errors = {
        config_id: status
        for config_id, status in health_data["fanout_statuses"].items()
        if _fanout_last_error(status)
    }
    return DebugHealthSummary(
        radio_state=radio_state,
        database_size_mb=health_data["database_size_mb"],
        oldest_undecrypted_timestamp=health_data["oldest_undecrypted_timestamp"],
        fanouts_with_errors=fanouts_with_errors,
        bots_disabled_source=health_data["bots_disabled_source"],
        basic_auth_enabled=health_data["basic_auth_enabled"],
    )


def _sanitize_radio_probe_self_info(self_info: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = dict(self_info or {})
    sanitized.pop("adv_lat", None)
    sanitized.pop("adv_lon", None)
    return sanitized


async def _build_contact_audit(
    observed_contacts_payload: dict[str, dict[str, Any]],
) -> DebugContactAudit:
    expected_contacts = await get_contacts_selected_for_radio_sync()
    expected_keys = {contact.public_key.lower() for contact in expected_contacts}
    observed_keys = {public_key.lower() for public_key in observed_contacts_payload}

    return DebugContactAudit(
        expected_and_found=len(expected_keys & observed_keys),
        expected_but_not_found=sorted(_sha256_hex(key) for key in (expected_keys - observed_keys)),
        found_but_not_expected=sorted(_sha256_hex(key) for key in (observed_keys - expected_keys)),
    )


async def _build_channel_audit(mc: Any, max_channels: int | None = None) -> DebugChannelAudit:
    cache_key_by_slot = {
        slot: channel_key for channel_key, slot in radio_runtime.get_channel_send_cache_snapshot()
    }

    matched_slots = 0
    wrong_slots: list[DebugChannelSlotMismatch] = []
    for slot in range(get_radio_channel_limit(max_channels)):
        event = await mc.commands.get_channel(slot)
        expected_key = cache_key_by_slot.get(slot)
        observed_key = _observed_channel_key(event)
        if expected_key == observed_key:
            matched_slots += 1
            continue
        wrong_slots.append(
            DebugChannelSlotMismatch(
                slot_number=slot,
                expected_sha256_of_room_key=_sha256_hex(expected_key) if expected_key else None,
                actual_sha256_of_room_key=_sha256_hex(observed_key) if observed_key else None,
            )
        )

    return DebugChannelAudit(
        matched_slots=matched_slots,
        wrong_slots=wrong_slots,
    )


async def _probe_radio() -> DebugRadioProbe:
    if not radio_runtime.is_connected:
        return DebugRadioProbe(performed=False, errors=["Radio not connected"])

    errors: list[str] = []
    try:
        async with radio_runtime.radio_operation(
            "debug_support_snapshot",
            suspend_auto_fetch=True,
        ) as mc:
            device_info = None
            stats_core = None
            stats_radio = None

            device_event = await mc.commands.send_device_query()
            if getattr(device_event, "type", None) == EventType.DEVICE_INFO:
                device_info = device_event.payload
            else:
                errors.append(f"send_device_query returned {_event_type_name(device_event)}")

            core_event = await mc.commands.get_stats_core()
            if getattr(core_event, "type", None) == EventType.STATS_CORE:
                stats_core = core_event.payload
            else:
                errors.append(f"get_stats_core returned {_event_type_name(core_event)}")

            radio_event = await mc.commands.get_stats_radio()
            if getattr(radio_event, "type", None) == EventType.STATS_RADIO:
                stats_radio = radio_event.payload
            else:
                errors.append(f"get_stats_radio returned {_event_type_name(radio_event)}")

            contacts_event = await mc.commands.get_contacts()
            observed_contacts_payload: dict[str, dict[str, Any]] = {}
            if getattr(contacts_event, "type", None) != EventType.ERROR:
                observed_contacts_payload = contacts_event.payload or {}
            else:
                errors.append(f"get_contacts returned {_event_type_name(contacts_event)}")

            return DebugRadioProbe(
                performed=True,
                errors=errors,
                self_info=_sanitize_radio_probe_self_info(mc.self_info),
                device_info=device_info,
                stats_core=stats_core,
                stats_radio=stats_radio,
                contacts=await _build_contact_audit(observed_contacts_payload),
                channels=await _build_channel_audit(
                    mc,
                    max_channels=_coerce_live_max_channels(device_info),
                ),
            )
    except Exception as exc:
        logger.warning("Debug support snapshot radio probe failed: %s", exc, exc_info=True)
        errors.append(str(exc))
        return DebugRadioProbe(performed=False, errors=errors)


@router.get("/debug", response_model=DebugSnapshotResponse)
async def debug_support_snapshot() -> DebugSnapshotResponse:
    """Return a support/debug snapshot with recent logs and live radio state."""
    connection_info = radio_runtime.connection_info
    connection_desired = radio_runtime.connection_desired
    setup_in_progress = radio_runtime.is_setup_in_progress
    setup_complete = radio_runtime.is_setup_complete
    radio_connected = radio_runtime.is_connected
    is_reconnecting = getattr(radio_runtime, "is_reconnecting", False)

    health_data = await build_health_data(radio_connected, connection_info)
    app_settings = await AppSettingsRepository.get()
    message_totals = await StatisticsRepository.get_database_message_totals()
    radio_probe = await _probe_radio()
    channels_with_incoming_messages = (
        await MessageRepository.count_channels_with_incoming_messages()
    )
    radio_state = _derive_debug_radio_state(
        radio_connected=radio_connected,
        connection_desired=connection_desired,
        setup_in_progress=setup_in_progress,
        setup_complete=setup_complete,
        is_reconnecting=is_reconnecting,
    )
    return DebugSnapshotResponse(
        captured_at=datetime.now(UTC).isoformat(),
        system=_build_system_info(),
        application=_build_application_info(),
        environment=_build_environment(),
        health=_build_debug_health_summary(health_data, radio_state=radio_state),
        settings=_build_debug_app_settings(app_settings),
        runtime=DebugRuntimeInfo(
            connection_info=connection_info,
            connection_desired=connection_desired,
            setup_in_progress=setup_in_progress,
            setup_complete=setup_complete,
            channels_with_incoming_messages=channels_with_incoming_messages,
            path_hash_mode_supported=radio_runtime.path_hash_mode_supported,
            channel_slot_reuse_enabled=radio_runtime.channel_slot_reuse_enabled(),
            channel_send_cache_capacity=radio_runtime.get_channel_send_cache_capacity(),
        ),
        database=DebugDatabaseInfo(
            total_dms=message_totals["total_dms"],
            total_channel_messages=message_totals["total_channel_messages"],
            total_outgoing=message_totals["total_outgoing"],
        ),
        radio_probe=radio_probe,
        logs=[*LOG_COPY_BOUNDARY_PREFIX, *get_recent_log_lines(limit=1000)],
    )
