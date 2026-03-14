import asyncio
import logging
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from meshcore import EventType

from app.dependencies import require_connected
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
)
from app.repository import ContactRepository
from app.routers.contacts import _ensure_on_radio, _resolve_contact_or_404
from app.services.radio_runtime import radio_runtime as radio_manager

if TYPE_CHECKING:
    from meshcore.events import Event

logger = logging.getLogger(__name__)

# ACL permission level names
ACL_PERMISSION_NAMES = {
    0: "Guest",
    1: "Read-only",
    2: "Read-write",
    3: "Admin",
}
router = APIRouter(prefix="/contacts", tags=["repeaters"])

# Delay between repeater radio operations to allow key exchange and path establishment
REPEATER_OP_DELAY_SECONDS = 2.0


def _monotonic() -> float:
    """Wrapper around time.monotonic() for testability.

    Patching time.monotonic directly breaks the asyncio event loop which also
    uses it. This indirection allows tests to control the clock safely.
    """
    return time.monotonic()


def _extract_response_text(event) -> str:
    """Extract text from a CLI response event, stripping the firmware '> ' prefix."""
    text = event.payload.get("text", str(event.payload))
    if text.startswith("> "):
        text = text[2:]
    return text


async def _fetch_repeater_response(
    mc,
    target_pubkey_prefix: str,
    timeout: float = 20.0,
) -> "Event | None":
    """Fetch a CLI response from a specific repeater via a validated get_msg() loop.

    Calls get_msg() repeatedly until a matching CLI response (txt_type=1) from the
    target repeater arrives or the wall-clock deadline expires. Unrelated messages
    are safe to skip — meshcore's event dispatcher already delivers them to the
    normal subscription handlers (on_contact_message, etc.) when get_msg() returns.

    Args:
        mc: MeshCore instance
        target_pubkey_prefix: 12-char hex prefix of the repeater's public key
        timeout: Wall-clock seconds before giving up

    Returns:
        The matching Event, or None if no response arrived before the deadline.
    """
    deadline = _monotonic() + timeout

    while _monotonic() < deadline:
        try:
            result = await mc.get_msg(timeout=2.0)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.debug("get_msg() exception: %s", e)
            await asyncio.sleep(1.0)
            continue

        if result.type == EventType.NO_MORE_MSGS:
            # No messages queued yet — wait and retry
            await asyncio.sleep(1.0)
            continue

        if result.type == EventType.ERROR:
            logger.debug("get_msg() error: %s", result.payload)
            await asyncio.sleep(1.0)
            continue

        if result.type == EventType.CONTACT_MSG_RECV:
            msg_prefix = result.payload.get("pubkey_prefix", "")
            txt_type = result.payload.get("txt_type", 0)
            if msg_prefix == target_pubkey_prefix and txt_type == 1:
                return result
            # Not our target — already dispatched to subscribers by meshcore,
            # so just continue draining the queue.
            logger.debug(
                "Skipping non-target message (from=%s, txt_type=%d) while waiting for %s",
                msg_prefix,
                txt_type,
                target_pubkey_prefix,
            )
            continue

        if result.type == EventType.CHANNEL_MSG_RECV:
            # Already dispatched to subscribers by meshcore; skip.
            logger.debug(
                "Skipping channel message (channel_idx=%s) during repeater fetch",
                result.payload.get("channel_idx"),
            )
            continue

        logger.debug("Unexpected event type %s during repeater fetch, skipping", result.type)

    logger.warning("No CLI response from repeater %s within %.1fs", target_pubkey_prefix, timeout)
    return None


async def prepare_repeater_connection(mc, contact: Contact, password: str) -> None:
    """Prepare connection to a repeater by adding to radio and logging in.

    Args:
        mc: MeshCore instance
        contact: The repeater contact
        password: Password for login (empty string for no password)

    Raises:
        HTTPException: If login fails
    """
    # Add contact to radio with path from DB (non-fatal — contact may already be loaded)
    logger.info("Adding repeater %s to radio", contact.public_key[:12])
    await _ensure_on_radio(mc, contact)

    # Send login with password
    logger.info("Sending login to repeater %s", contact.public_key[:12])
    login_result = await mc.send_login(contact.public_key, password)

    if login_result.type == EventType.ERROR:
        raise HTTPException(status_code=401, detail=f"Login failed: {login_result.payload}")

    # Wait for key exchange to complete before sending requests
    logger.debug("Waiting %.1fs for key exchange to complete", REPEATER_OP_DELAY_SECONDS)
    await asyncio.sleep(REPEATER_OP_DELAY_SECONDS)


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
    """Log in to a repeater. Adds contact to radio, sends login, waits for key exchange."""
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        await prepare_repeater_connection(mc, contact, request.password)

    return RepeaterLoginResponse(status="ok")


@router.post("/{public_key}/repeater/status", response_model=RepeaterStatusResponse)
async def repeater_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a repeater (single attempt, 10s timeout)."""
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_status", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        status = await mc.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

    if status is None:
        raise HTTPException(status_code=504, detail="No status response from repeater")
    # SpiBackend returns _Event(STATUS_RESPONSE, payload); unwrap so we always have a dict
    status = getattr(status, "payload", status)

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
    )


@router.post("/{public_key}/repeater/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def repeater_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP sensor telemetry from a repeater (single attempt, 10s timeout)."""
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await _ensure_on_radio(mc, contact)

        telemetry = await mc.req_telemetry_sync(contact.public_key, timeout=10, min_timeout=5)

    if telemetry is None:
        raise HTTPException(status_code=504, detail="No telemetry response from repeater")
    # SpiBackend returns _Event(TELEMETRY_RESPONSE, payload); unwrap so we iterate the list
    telemetry = getattr(telemetry, "payload", telemetry)
    if not isinstance(telemetry, list):
        telemetry = []

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
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_neighbors", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        neighbors_data = await mc.fetch_all_neighbours(
            contact.public_key, timeout=10, min_timeout=5
        )
    # SpiBackend may return _Event(..., payload); unwrap so we have a dict
    neighbors_data = getattr(neighbors_data, "payload", neighbors_data)

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
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_acl", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await _ensure_on_radio(mc, contact)

        acl_data = await mc.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

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
    """Send a batch of CLI commands to a repeater and collect responses.

    Opens a radio operation with polling paused and auto-fetch suspended (since
    we call get_msg() directly via _fetch_repeater_response), adds the contact
    to the radio for routing, then sends each command sequentially with a 1-second
    gap between them.

    Returns a dict mapping field names to response strings (or None on timeout).
    """
    results: dict[str, str | None] = {field: None for _, field in commands}

    async with radio_manager.radio_operation(
        operation_name,
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        await _ensure_on_radio(mc, contact)
        await asyncio.sleep(1.0)

        for i, (cmd, field) in enumerate(commands):
            if i > 0:
                await asyncio.sleep(1.0)

            send_result = await mc.send_cmd(contact.public_key, cmd)
            if send_result.type == EventType.ERROR:
                logger.debug("Command '%s' send error: %s", cmd, send_result.payload)
                continue

            response_event = await _fetch_repeater_response(
                mc, contact.public_key[:12], timeout=10.0
            )
            if response_event is not None:
                results[field] = _extract_response_text(response_event)
            else:
                logger.warning("No response for command '%s' (%s)", cmd, field)

    return results


@router.post("/{public_key}/repeater/node-info", response_model=RepeaterNodeInfoResponse)
async def repeater_node_info(public_key: str) -> RepeaterNodeInfoResponse:
    """Fetch repeater identity/location info via a small CLI batch."""
    require_connected()
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
    require_connected()
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
    require_connected()
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
    require_connected()
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
    """Send a CLI command to a repeater.

    The contact must be a repeater (type=2). The user must have already logged in
    via the repeater/login endpoint. This endpoint ensures the contact is on the
    radio before sending commands (the repeater remembers ACL permissions after login).

    Common commands:
    - get name, set name <value>
    - get tx, set tx <dbm>
    - get radio, set radio <freq,bw,sf,cr>
    - tempradio <freq,bw,sf,cr,minutes>
    - setperm <pubkey> <permission>  (0=guest, 1=read-only, 2=read-write, 3=admin)
    - clock, clock sync, time <epoch_seconds>
    - reboot
    - ver
    """
    require_connected()

    # Get contact from database
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "send_repeater_command",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        # Add contact to radio with path from DB (non-fatal — contact may already be loaded)
        logger.info("Adding repeater %s to radio", contact.public_key[:12])
        await _ensure_on_radio(mc, contact)
        await asyncio.sleep(1.0)

        # Send the command
        logger.info("Sending command to repeater %s: %s", contact.public_key[:12], request.command)

        send_result = await mc.send_cmd(contact.public_key, request.command)

        if send_result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500, detail=f"Failed to send command: {send_result.payload}"
            )

        # Wait for response using validated fetch loop
        response_event = await _fetch_repeater_response(mc, contact.public_key[:12])

        if response_event is None:
            logger.warning(
                "No response from repeater %s for command: %s",
                contact.public_key[:12],
                request.command,
            )
            return CommandResponse(
                command=request.command,
                response="(no response - command may have been processed)",
            )

        # CONTACT_MSG_RECV payloads use sender_timestamp in meshcore.
        response_text = _extract_response_text(response_event)
        sender_timestamp = response_event.payload.get(
            "sender_timestamp",
            response_event.payload.get("timestamp"),
        )
        logger.info("Received response from %s: %s", contact.public_key[:12], response_text)

        return CommandResponse(
            command=request.command,
            response=response_text,
            sender_timestamp=sender_timestamp,
        )
