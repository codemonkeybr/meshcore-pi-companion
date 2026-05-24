from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import (
    CONTACT_TYPE_ROOM,
    AclEntry,
    LppSensor,
    RepeaterAclResponse,
    RepeaterLoginRequest,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterStatusResponse,
)
from app.routers.contacts import _ensure_on_radio, _resolve_contact_or_404
from app.routers.server_control import (
    prepare_authenticated_contact_connection,
    require_server_capable_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager

router = APIRouter(prefix="/contacts", tags=["rooms"])


def _require_room(contact) -> None:
    require_server_capable_contact(contact, allowed_types=(CONTACT_TYPE_ROOM,))


def _meshcore_from_backend(be) -> Any:
    """Return the underlying MeshCore; ClientBackend has _mc, SpiBackend is passed through."""
    from app.radio_backend import RadioBackend

    if isinstance(be, RadioBackend):
        return getattr(be, "_mc", be)
    return be


@router.post("/{public_key}/room/login", response_model=RepeaterLoginResponse)
async def room_login(public_key: str, request: RepeaterLoginRequest) -> RepeaterLoginResponse:
    """Attempt room-server login and report whether auth was confirmed."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as be:
        mc = _meshcore_from_backend(be)
        return await prepare_authenticated_contact_connection(
            mc,
            contact,
            request.password,
            label="room server",
        )


@router.post("/{public_key}/room/status", response_model=RepeaterStatusResponse)
async def room_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_status", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        await _ensure_on_radio(mc, contact)
        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

    if status is None:
        raise HTTPException(status_code=408, detail="No status response from room server")

    return RepeaterStatusResponse(
        battery_volts=status.get("bat", 0) / 1000.0,
        tx_queue_len=status.get("tx_queue_len", 0),
        noise_floor_dbm=status.get("noise_floor", 0),
        last_rssi_dbm=status.get("last_rssi", 0),
        last_snr_db=status.get("last_snr", 0.0),
        packets_received=status.get("nb_recv", 0),
        packets_sent=status.get("nb_sent", 0),
        airtime_seconds=status.get("airtime", 0),
        rx_airtime_seconds=status.get("rx_airtime", 0),
        uptime_seconds=status.get("uptime", 0),
        sent_flood=status.get("sent_flood", 0),
        sent_direct=status.get("sent_direct", 0),
        recv_flood=status.get("recv_flood", 0),
        recv_direct=status.get("recv_direct", 0),
        flood_dups=status.get("flood_dups", 0),
        direct_dups=status.get("direct_dups", 0),
        full_events=status.get("full_evts", 0),
        recv_errors=status.get("recv_errors"),
    )


@router.post("/{public_key}/room/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def room_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP telemetry from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        await _ensure_on_radio(mc, contact)
        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        raise HTTPException(status_code=408, detail="No telemetry response from room server")

    sensors = [
        LppSensor(
            channel=entry.get("channel", 0),
            type_name=str(entry.get("type", "unknown")),
            value=entry.get("value", 0),
        )
        for entry in telemetry
    ]
    return RepeaterLppTelemetryResponse(sensors=sensors)


@router.post("/{public_key}/room/acl", response_model=RepeaterAclResponse)
async def room_acl(public_key: str) -> RepeaterAclResponse:
    """Fetch ACL entries from a room server."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_acl", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        await _ensure_on_radio(mc, contact)
        acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

    acl_entries = []
    if acl_data and isinstance(acl_data, list):
        from app.repository import ContactRepository
        from app.routers.repeaters import ACL_PERMISSION_NAMES

        for entry in acl_data:
            pubkey_prefix = entry.get("key", "")
            perm = entry.get("perm", 0)
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            acl_entries.append(
                AclEntry(
                    pubkey_prefix=pubkey_prefix,
                    name=resolved_contact.name if resolved_contact else None,
                    permission=perm,
                    permission_name=ACL_PERMISSION_NAMES.get(perm, f"Unknown({perm})"),
                )
            )

    return RepeaterAclResponse(acl=acl_entries)
