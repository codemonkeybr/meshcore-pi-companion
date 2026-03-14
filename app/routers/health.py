import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.repository import RawPacketRepository
from app.services.radio_runtime import radio_runtime as radio_manager

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    radio_connected: bool
    radio_initializing: bool = False
    radio_state: str = "disconnected"
    connection_info: str | None
    database_size_mb: float
    oldest_undecrypted_timestamp: int | None
    fanout_statuses: dict[str, dict[str, str]] = {}
    bots_disabled: bool = False
    setup_required: bool = False


async def build_health_data(radio_connected: bool, connection_info: str | None) -> dict:
    """Build the health status payload used by REST endpoint and WebSocket broadcasts."""
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
    try:
        from app.fanout.manager import fanout_manager

        fanout_statuses = fanout_manager.get_statuses()
    except Exception:
        pass

    setup_in_progress = getattr(radio_manager, "is_setup_in_progress", False)
    if not isinstance(setup_in_progress, bool):
        setup_in_progress = False

    setup_complete = getattr(radio_manager, "is_setup_complete", radio_connected)
    if not isinstance(setup_complete, bool):
        setup_complete = radio_connected
    if not radio_connected:
        setup_complete = False

    connection_desired = getattr(radio_manager, "connection_desired", True)
    if not isinstance(connection_desired, bool):
        connection_desired = True

    is_reconnecting = getattr(radio_manager, "is_reconnecting", False)
    if not isinstance(is_reconnecting, bool):
        is_reconnecting = False

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

    out: dict[str, Any] = {
        "status": "ok" if radio_connected and not radio_initializing else "degraded",
        "radio_connected": radio_connected,
        "radio_initializing": radio_initializing,
        "radio_state": radio_state,
        "connection_info": connection_info,
        "database_size_mb": db_size_mb,
        "oldest_undecrypted_timestamp": oldest_ts,
        "fanout_statuses": fanout_statuses,
        "bots_disabled": settings.disable_bots,
    }
    if settings.connection_type == "spi":
        from app.routers.setup import get_setup_required

        out["setup_required"] = get_setup_required()
    else:
        out["setup_required"] = False
    return out


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    """Check if the API is running and if the radio is connected."""
    data = await build_health_data(radio_manager.is_connected, radio_manager.connection_info)
    return HealthResponse(**data)
