import os
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.repository import RawPacketRepository
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.radio_stats import get_latest_radio_stats
from app.version_info import get_app_build_info

router = APIRouter(tags=["health"])


class RadioDeviceInfoResponse(BaseModel):
    model: str | None = None
    firmware_build: str | None = None
    firmware_version: str | None = None
    max_contacts: int | None = None
    max_channels: int | None = None


class AppInfoResponse(BaseModel):
    version: str
    commit_hash: str | None = None


class FanoutStatusResponse(BaseModel):
    name: str
    type: str
    status: str
    last_error: str | None = None


class RadioStatsSnapshot(BaseModel):
    """Latest cached stats from the local radio's periodic 60s poll."""

    timestamp: int | None = None
    # Core stats
    battery_mv: int | None = None
    uptime_secs: int | None = None
    queue_len: int | None = None
    errors: int | None = None
    # Radio stats
    noise_floor: int | None = None
    last_rssi: int | None = None
    last_snr: float | None = None
    tx_air_secs: int | None = None
    rx_air_secs: int | None = None
    # Packet stats
    packets_recv: int | None = None
    packets_sent: int | None = None
    flood_tx: int | None = None
    direct_tx: int | None = None
    flood_rx: int | None = None
    direct_rx: int | None = None


class HealthResponse(BaseModel):
    status: str
    radio_connected: bool
    radio_initializing: bool = False
    radio_state: str = "disconnected"
    connection_info: str | None
    app_info: AppInfoResponse | None = None
    radio_device_info: RadioDeviceInfoResponse | None = None
    radio_stats: RadioStatsSnapshot | None = None
    database_size_mb: float
    oldest_undecrypted_timestamp: int | None
    fanout_statuses: dict[str, FanoutStatusResponse] = Field(default_factory=dict)
    bots_disabled: bool = False
    bots_disabled_source: Literal["env", "until_restart"] | None = None
    basic_auth_enabled: bool = False


def _clean_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _read_optional_bool_setting(name: str) -> bool:
    value = getattr(settings, name, False)
    return value if isinstance(value, bool) else False


async def build_health_data(radio_connected: bool, connection_info: str | None) -> dict:
    """Build the health status payload used by REST endpoint and WebSocket broadcasts."""
    app_build_info = get_app_build_info()
    db_size_mb = 0.0
    try:
        db_size_bytes = os.path.getsize(settings.database_path)
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    except OSError:
        pass

    oldest_ts = None
    try:
        oldest_ts = await RawPacketRepository.get_oldest_undecrypted()
    except RuntimeError:
        pass  # Database not connected

    # Fanout module statuses
    fanout_statuses: dict[str, Any] = {}
    bots_disabled_source = "env" if _read_optional_bool_setting("disable_bots") else None
    try:
        from app.fanout.manager import fanout_manager

        fanout_statuses = fanout_manager.get_statuses()
        manager_bots_disabled_source = fanout_manager.get_bots_disabled_source()
        if manager_bots_disabled_source is not None:
            bots_disabled_source = manager_bots_disabled_source
    except Exception:
        pass

    setup_in_progress = getattr(radio_manager, "is_setup_in_progress", False)
    setup_complete = getattr(radio_manager, "is_setup_complete", radio_connected)
    if not radio_connected:
        setup_complete = False

    connection_desired = getattr(radio_manager, "connection_desired", True)
    is_reconnecting = getattr(radio_manager, "is_reconnecting", False)

    radio_initializing = bool(radio_connected and (setup_in_progress or not setup_complete))
    if not connection_desired:
        radio_state = "paused"
    elif radio_initializing:
        radio_state = "initializing"
    elif radio_connected:
        radio_state = "connected"
    elif is_reconnecting:
        radio_state = "connecting"
    else:
        radio_state = "disconnected"

    radio_device_info = None
    device_info_loaded = getattr(radio_manager, "device_info_loaded", False)
    if radio_connected and device_info_loaded:
        radio_device_info = {
            "model": _clean_optional_str(getattr(radio_manager, "device_model", None)),
            "firmware_build": _clean_optional_str(getattr(radio_manager, "firmware_build", None)),
            "firmware_version": _clean_optional_str(
                getattr(radio_manager, "firmware_version", None)
            ),
            "max_contacts": getattr(radio_manager, "max_contacts", None),
            "max_channels": getattr(radio_manager, "max_channels", None),
        }

    # Local radio stats from the 60s background sampler
    raw_stats = get_latest_radio_stats()
    radio_stats = None
    if raw_stats:
        packets = raw_stats.get("packets") or {}
        radio_stats = {
            "timestamp": raw_stats.get("timestamp"),
            "battery_mv": raw_stats.get("battery_mv"),
            "uptime_secs": raw_stats.get("uptime_secs"),
            "queue_len": raw_stats.get("queue_len"),
            "errors": raw_stats.get("errors"),
            "noise_floor": raw_stats.get("noise_floor"),
            "last_rssi": raw_stats.get("last_rssi"),
            "last_snr": raw_stats.get("last_snr"),
            "tx_air_secs": raw_stats.get("tx_air_secs"),
            "rx_air_secs": raw_stats.get("rx_air_secs"),
            "packets_recv": packets.get("recv"),
            "packets_sent": packets.get("sent"),
            "flood_tx": packets.get("flood_tx"),
            "direct_tx": packets.get("direct_tx"),
            "flood_rx": packets.get("flood_rx"),
            "direct_rx": packets.get("direct_rx"),
        }

    return {
        "status": "ok" if radio_connected and not radio_initializing else "degraded",
        "radio_connected": radio_connected,
        "radio_initializing": radio_initializing,
        "radio_state": radio_state,
        "connection_info": connection_info,
        "app_info": {
            "version": app_build_info.version,
            "commit_hash": app_build_info.commit_hash,
        },
        "radio_device_info": radio_device_info,
        "radio_stats": radio_stats,
        "database_size_mb": db_size_mb,
        "oldest_undecrypted_timestamp": oldest_ts,
        "fanout_statuses": fanout_statuses,
        "bots_disabled": bots_disabled_source is not None,
        "bots_disabled_source": bots_disabled_source,
        "basic_auth_enabled": _read_optional_bool_setting("basic_auth_enabled"),
    }


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    """Check if the API is running and if the radio is connected."""
    data = await build_health_data(radio_manager.is_connected, radio_manager.connection_info)
    return HealthResponse(**data)
