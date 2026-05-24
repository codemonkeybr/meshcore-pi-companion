import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import (
    CONTACT_TYPE_REPEATER,
    AclEntry,
    CommandRequest,
    CommandResponse,
    Contact,
    LppSensor,
    NeighborInfo,
    RepeaterAclResponse,
    RepeaterAdvertIntervalsResponse,
    RepeaterLoginRequest,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterNeighborsResponse,
    RepeaterNodeInfoResponse,
    RepeaterOwnerInfoResponse,
    RepeaterRadioSettingsResponse,
    RepeaterStatusResponse,
    TelemetryHistoryEntry,
)
from app.repository import ContactRepository, RepeaterTelemetryRepository
from app.routers.contacts import _ensure_on_radio, _resolve_contact_or_404
from app.routers.server_control import (
    batch_cli_fetch,
    prepare_authenticated_contact_connection,
    require_server_capable_contact,
    send_contact_cli_command,
)
from app.services.radio_runtime import radio_runtime as radio_manager

logger = logging.getLogger(__name__)


def _meshcore_from_backend(be) -> Any:
    """Return the underlying MeshCore; ClientBackend has _mc, SpiBackend is passed through."""
    from app.radio_backend import RadioBackend

    if isinstance(be, RadioBackend):
        return getattr(be, "_mc", be)
    return be


# ACL permission level names
ACL_PERMISSION_NAMES = {
    0: "Guest",
    1: "Read-only",
    2: "Read-write",
    3: "Admin",
}
router = APIRouter(prefix="/contacts", tags=["repeaters"])
REPEATER_LOGIN_RESPONSE_TIMEOUT_SECONDS = 5.0


async def prepare_repeater_connection(mc, contact: Contact, password: str) -> RepeaterLoginResponse:
    return await prepare_authenticated_contact_connection(
        mc,
        contact,
        password,
        label="repeater",
        response_timeout=REPEATER_LOGIN_RESPONSE_TIMEOUT_SECONDS,
    )


def _require_repeater(contact: Contact) -> None:
    """Raise 400 if contact is not a repeater."""
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})",
        )


# ---------------------------------------------------------------------------
# Granular repeater endpoints — one attempt, no server-side retries.
# Frontend manages retry logic for better UX control.
# ---------------------------------------------------------------------------


@router.post("/{public_key}/repeater/login", response_model=RepeaterLoginResponse)
async def repeater_login(public_key: str, request: RepeaterLoginRequest) -> RepeaterLoginResponse:
    """Attempt repeater login and report whether auth was confirmed."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as be:
        mc = _meshcore_from_backend(be)
        return await prepare_repeater_connection(mc, contact, request.password)


@router.post("/{public_key}/repeater/status", response_model=RepeaterStatusResponse)
async def repeater_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    lpp_raw = None
    async with radio_manager.radio_operation(
        "repeater_status", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

        # Best-effort LPP sensor fetch while we still hold the lock
        if status is not None:
            try:
                lpp_raw = await mc.commands.req_telemetry_sync(
                    contact.public_key, timeout=10, min_timeout=5
                )
            except Exception as e:
                logger.debug("LPP sensor fetch failed for %s (non-fatal): %s", public_key[:12], e)

    if status is None:
        raise HTTPException(status_code=408, detail="No status response from repeater")

    response = RepeaterStatusResponse(
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

    # Record to telemetry history as a JSON blob (best-effort)
    now = int(time.time())
    status_dict = response.model_dump(exclude={"telemetry_history"})

    # Attach scalar LPP sensors to the stored snapshot (same logic as auto-collect)
    if lpp_raw:
        lpp_sensors = []
        for entry in lpp_raw:
            value = entry.get("value", 0)
            if isinstance(value, dict):
                continue
            lpp_sensors.append(
                {
                    "channel": entry.get("channel", 0),
                    "type_name": str(entry.get("type", "unknown")),
                    "value": value,
                }
            )
        if lpp_sensors:
            status_dict["lpp_sensors"] = lpp_sensors

    try:
        await RepeaterTelemetryRepository.record(
            public_key=contact.public_key,
            timestamp=now,
            data=status_dict,
        )

        # Dispatch to fanout modules (e.g. HA MQTT discovery)
        from app.fanout.manager import fanout_manager

        asyncio.create_task(
            fanout_manager.broadcast_telemetry(
                {
                    "public_key": contact.public_key,
                    "name": contact.name or contact.public_key[:12],
                    "timestamp": now,
                    **status_dict,
                }
            )
        )
    except Exception as e:
        logger.warning("Failed to record telemetry history: %s", e)

    # Fetch recent history and embed in response
    try:
        since = now - 30 * 86400  # last 30 days
        rows = await RepeaterTelemetryRepository.get_history(contact.public_key, since)
        response.telemetry_history = [TelemetryHistoryEntry(**row) for row in rows]
    except Exception as e:
        logger.warning("Failed to fetch telemetry history: %s", e)

    return response


@router.get(
    "/{public_key}/repeater/telemetry-history",
    response_model=list[TelemetryHistoryEntry],
)
async def repeater_telemetry_history(public_key: str) -> list[TelemetryHistoryEntry]:
    """Return stored telemetry history for a repeater (read-only, no radio access)."""
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    since = int(time.time()) - 30 * 86400
    rows = await RepeaterTelemetryRepository.get_history(contact.public_key, since)
    return [TelemetryHistoryEntry(**row) for row in rows]


@router.post("/{public_key}/repeater/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def repeater_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP sensor telemetry from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        await _ensure_on_radio(mc, contact)

        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        raise HTTPException(status_code=408, detail="No telemetry response from repeater")

    sensors: list[LppSensor] = []
    for entry in telemetry:
        channel = entry.get("channel", 0)
        type_name = str(entry.get("type", "unknown"))
        value = entry.get("value", 0)
        sensors.append(LppSensor(channel=channel, type_name=type_name, value=value))

    return RepeaterLppTelemetryResponse(sensors=sensors)


@router.post("/{public_key}/repeater/neighbors", response_model=RepeaterNeighborsResponse)
async def repeater_neighbors(public_key: str) -> RepeaterNeighborsResponse:
    """Fetch neighbors from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_neighbors", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        neighbors_data = await mc.commands.fetch_all_neighbours(
            contact.public_key, timeout=10, min_timeout=5
        )

    neighbors: list[NeighborInfo] = []
    if neighbors_data and "neighbours" in neighbors_data:
        for n in neighbors_data["neighbours"]:
            pubkey_prefix = n.get("pubkey", "")
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            neighbors.append(
                NeighborInfo(
                    pubkey_prefix=pubkey_prefix,
                    name=resolved_contact.name if resolved_contact else None,
                    snr=n.get("snr", 0.0),
                    last_heard_seconds=n.get("secs_ago", 0),
                )
            )

    return RepeaterNeighborsResponse(neighbors=neighbors)


@router.post("/{public_key}/repeater/acl", response_model=RepeaterAclResponse)
async def repeater_acl(public_key: str) -> RepeaterAclResponse:
    """Fetch ACL from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_acl", pause_polling=True, suspend_auto_fetch=True
    ) as be:
        mc = _meshcore_from_backend(be)
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

    acl_entries: list[AclEntry] = []
    if acl_data and isinstance(acl_data, list):
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


async def _batch_cli_fetch(
    contact: Contact,
    operation_name: str,
    commands: list[tuple[str, str]],
) -> dict[str, str | None]:
    return await batch_cli_fetch(contact, operation_name, commands)


@router.post("/{public_key}/repeater/node-info", response_model=RepeaterNodeInfoResponse)
async def repeater_node_info(public_key: str) -> RepeaterNodeInfoResponse:
    """Fetch repeater identity/location info via a small CLI batch."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_node_info",
        [
            ("get name", "name"),
            ("get lat", "lat"),
            ("get lon", "lon"),
            ("clock", "clock_utc"),
        ],
    )
    return RepeaterNodeInfoResponse(**results)


@router.post("/{public_key}/repeater/radio-settings", response_model=RepeaterRadioSettingsResponse)
async def repeater_radio_settings(public_key: str) -> RepeaterRadioSettingsResponse:
    """Fetch radio settings from a repeater via radio/config CLI commands."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_radio_settings",
        [
            ("ver", "firmware_version"),
            ("get radio", "radio"),
            ("get tx", "tx_power"),
            ("get af", "airtime_factor"),
            ("get repeat", "repeat_enabled"),
            ("get flood.max", "flood_max"),
        ],
    )
    return RepeaterRadioSettingsResponse(**results)


@router.post(
    "/{public_key}/repeater/advert-intervals", response_model=RepeaterAdvertIntervalsResponse
)
async def repeater_advert_intervals(public_key: str) -> RepeaterAdvertIntervalsResponse:
    """Fetch advertisement intervals from a repeater via CLI commands."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_advert_intervals",
        [
            ("get advert.interval", "advert_interval"),
            ("get flood.advert.interval", "flood_advert_interval"),
        ],
    )
    return RepeaterAdvertIntervalsResponse(**results)


@router.post("/{public_key}/repeater/owner-info", response_model=RepeaterOwnerInfoResponse)
async def repeater_owner_info(public_key: str) -> RepeaterOwnerInfoResponse:
    """Fetch owner info and guest password from a repeater via CLI commands."""
    radio_manager.require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_owner_info",
        [
            ("get owner.info", "owner_info"),
            ("get guest.password", "guest_password"),
        ],
    )
    return RepeaterOwnerInfoResponse(**results)


@router.post("/{public_key}/command", response_model=CommandResponse)
async def send_repeater_command(public_key: str, request: CommandRequest) -> CommandResponse:
    """Send a CLI command to a repeater or room server."""
    radio_manager.require_connected()

    contact = await _resolve_contact_or_404(public_key)
    require_server_capable_contact(contact)
    return await send_contact_cli_command(
        contact,
        request.command,
        operation_name="send_repeater_command",
    )
