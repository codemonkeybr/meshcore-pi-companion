import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import (
    CONTACT_TYPE_REPEATER,
    AclEntry,
    CommandRequest,
    CommandResponse,
    Contact,
    ContactActiveRoom,
    ContactAdvertPath,
    ContactAdvertPathSummary,
    ContactDetail,
    CreateContactRequest,
    LppSensor,
    NearestRepeater,
    NeighborInfo,
    RepeaterAclResponse,
    RepeaterAdvertIntervalsResponse,
    RepeaterLoginRequest,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterNeighborsResponse,
    RepeaterOwnerInfoResponse,
    RepeaterRadioSettingsResponse,
    RepeaterStatusResponse,
    TraceResponse,
)
from app.packet_processor import start_historical_dm_decryption
from app.radio import radio_manager
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    ContactAdvertPathRepository,
    ContactNameHistoryRepository,
    ContactRepository,
    MessageRepository,
)

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
router = APIRouter(prefix="/contacts", tags=["contacts"])

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
            result = await mc.commands.get_msg(timeout=2.0)
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


def _ambiguous_contact_detail(err: AmbiguousPublicKeyPrefixError) -> str:
    sample = ", ".join(key[:12] for key in err.matches[:2])
    return (
        f"Ambiguous contact key prefix '{err.prefix}'. "
        f"Use a full 64-character public key. Matching contacts: {sample}"
    )


async def _resolve_contact_or_404(
    public_key: str, not_found_detail: str = "Contact not found"
) -> Contact:
    try:
        contact = await ContactRepository.get_by_key_or_prefix(public_key)
    except AmbiguousPublicKeyPrefixError as err:
        raise HTTPException(status_code=409, detail=_ambiguous_contact_detail(err)) from err
    if not contact:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return contact


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
    login_result = await mc.commands.send_login(contact.public_key, password)

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


async def _ensure_on_radio(mc, contact: Contact) -> None:
    """Add a contact to the radio for routing, raising 500 on failure."""
    add_result = await mc.commands.add_contact(contact.to_radio_dict())
    if add_result is not None and add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500, detail=f"Failed to add contact to radio: {add_result.payload}"
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

        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

    if status is None:
        raise HTTPException(status_code=504, detail="No status response from repeater")

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

        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        raise HTTPException(status_code=504, detail="No telemetry response from repeater")

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
    require_connected()
    contact = await _resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_acl", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
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

            send_result = await mc.commands.send_cmd(contact.public_key, cmd)
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


@router.post("/{public_key}/repeater/radio-settings", response_model=RepeaterRadioSettingsResponse)
async def repeater_radio_settings(public_key: str) -> RepeaterRadioSettingsResponse:
    """Fetch radio settings from a repeater via batch CLI commands."""
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
            ("get name", "name"),
            ("get lat", "lat"),
            ("get lon", "lon"),
            ("clock", "clock_utc"),
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


@router.get("", response_model=list[Contact])
async def list_contacts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    """List contacts from the database."""
    return await ContactRepository.get_all(limit=limit, offset=offset)


@router.get("/repeaters/advert-paths", response_model=list[ContactAdvertPathSummary])
async def list_repeater_advert_paths(
    limit_per_repeater: int = Query(default=10, ge=1, le=50),
) -> list[ContactAdvertPathSummary]:
    """List recent unique advert paths for all repeaters.

    Note: This endpoint now returns paths for all contacts (table was renamed).
    The route is kept for backward compatibility.
    """
    return await ContactAdvertPathRepository.get_recent_for_all_contacts(
        limit_per_contact=limit_per_repeater
    )


@router.post("", response_model=Contact)
async def create_contact(
    request: CreateContactRequest, background_tasks: BackgroundTasks
) -> Contact:
    """Create a new contact in the database.

    If the contact already exists, updates the name (if provided).
    If try_historical is True, attempts to decrypt historical DM packets.
    """
    # Validate hex format
    try:
        bytes.fromhex(request.public_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid public key: must be valid hex") from e

    # Check if contact already exists
    existing = await ContactRepository.get_by_key(request.public_key)
    if existing:
        # Update name if provided
        if request.name:
            await ContactRepository.upsert(
                {
                    "public_key": existing.public_key,
                    "name": request.name,
                    "type": existing.type,
                    "flags": existing.flags,
                    "last_path": existing.last_path,
                    "last_path_len": existing.last_path_len,
                    "last_advert": existing.last_advert,
                    "lat": existing.lat,
                    "lon": existing.lon,
                    "last_seen": existing.last_seen,
                    "on_radio": existing.on_radio,
                    "last_contacted": existing.last_contacted,
                }
            )
            existing.name = request.name

        # Trigger historical decryption if requested (even for existing contacts)
        if request.try_historical:
            await start_historical_dm_decryption(
                background_tasks, request.public_key, request.name or existing.name
            )

        return existing

    # Create new contact
    lower_key = request.public_key.lower()
    contact_data = {
        "public_key": lower_key,
        "name": request.name,
        "type": 0,  # Unknown
        "flags": 0,
        "last_path": None,
        "last_path_len": -1,
        "last_advert": None,
        "lat": None,
        "lon": None,
        "last_seen": None,
        "on_radio": False,
        "last_contacted": None,
    }
    await ContactRepository.upsert(contact_data)
    logger.info("Created contact %s", lower_key[:12])

    # Promote any prefix-stored messages to this full key
    claimed = await MessageRepository.claim_prefix_messages(lower_key)
    if claimed > 0:
        logger.info("Claimed %d prefix messages for contact %s", claimed, lower_key[:12])

    # Trigger historical decryption if requested
    if request.try_historical:
        await start_historical_dm_decryption(background_tasks, lower_key, request.name)

    return Contact(**contact_data)


@router.get("/{public_key}/detail", response_model=ContactDetail)
async def get_contact_detail(public_key: str) -> ContactDetail:
    """Get comprehensive contact profile data.

    Returns contact info, name history, message counts, most active rooms,
    advertisement paths, advert frequency, and nearest repeaters.
    """
    contact = await _resolve_contact_or_404(public_key)

    name_history = await ContactNameHistoryRepository.get_history(contact.public_key)
    dm_count = await MessageRepository.count_dm_messages(contact.public_key)
    chan_count = await MessageRepository.count_channel_messages_by_sender(contact.public_key)
    active_rooms_raw = await MessageRepository.get_most_active_rooms(contact.public_key)
    advert_paths = await ContactAdvertPathRepository.get_recent_for_contact(contact.public_key)

    most_active_rooms = [
        ContactActiveRoom(channel_key=key, channel_name=name, message_count=count)
        for key, name, count in active_rooms_raw
    ]

    # Compute advert observation rate (observations/hour) from path data.
    # Note: a single advertisement can arrive via multiple paths, so this counts
    # RF observations, not unique advertisement broadcasts.
    advert_frequency: float | None = None
    if advert_paths:
        total_observations = sum(p.heard_count for p in advert_paths)
        earliest = min(p.first_seen for p in advert_paths)
        latest = max(p.last_seen for p in advert_paths)
        span_hours = (latest - earliest) / 3600.0
        if span_hours > 0:
            advert_frequency = round(total_observations / span_hours, 2)

    # Compute nearest repeaters from first-hop prefixes in advert paths
    first_hop_stats: dict[str, dict] = {}  # prefix -> {heard_count, path_len, last_seen}
    for p in advert_paths:
        if p.path and len(p.path) >= 2:
            prefix = p.path[:2].lower()
            if prefix not in first_hop_stats:
                first_hop_stats[prefix] = {
                    "heard_count": 0,
                    "path_len": p.path_len,
                    "last_seen": p.last_seen,
                }
            first_hop_stats[prefix]["heard_count"] += p.heard_count
            first_hop_stats[prefix]["last_seen"] = max(
                first_hop_stats[prefix]["last_seen"], p.last_seen
            )

    # Resolve all first-hop prefixes to contacts in a single query
    resolved_contacts = await ContactRepository.resolve_prefixes(list(first_hop_stats.keys()))

    nearest_repeaters: list[NearestRepeater] = []
    for prefix, stats in first_hop_stats.items():
        resolved = resolved_contacts.get(prefix)
        nearest_repeaters.append(
            NearestRepeater(
                public_key=resolved.public_key if resolved else prefix,
                name=resolved.name if resolved else None,
                path_len=stats["path_len"],
                last_seen=stats["last_seen"],
                heard_count=stats["heard_count"],
            )
        )

    nearest_repeaters.sort(key=lambda r: r.heard_count, reverse=True)

    return ContactDetail(
        contact=contact,
        name_history=name_history,
        dm_message_count=dm_count,
        channel_message_count=chan_count,
        most_active_rooms=most_active_rooms,
        advert_paths=advert_paths,
        advert_frequency=advert_frequency,
        nearest_repeaters=nearest_repeaters,
    )


@router.get("/{public_key}", response_model=Contact)
async def get_contact(public_key: str) -> Contact:
    """Get a specific contact by public key or prefix."""
    return await _resolve_contact_or_404(public_key)


@router.get("/{public_key}/advert-paths", response_model=list[ContactAdvertPath])
async def get_contact_advert_paths(
    public_key: str,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[ContactAdvertPath]:
    """List recent unique advert paths for a contact."""
    contact = await _resolve_contact_or_404(public_key)
    return await ContactAdvertPathRepository.get_recent_for_contact(contact.public_key, limit)


@router.post("/sync")
async def sync_contacts_from_radio() -> dict:
    """Sync contacts from the radio to the database."""
    require_connected()

    logger.info("Syncing contacts from radio")

    async with radio_manager.radio_operation("sync_contacts_from_radio") as mc:
        result = await mc.commands.get_contacts()

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to get contacts: {result.payload}")

    contacts = result.payload
    count = 0

    for public_key, contact_data in contacts.items():
        lower_key = public_key.lower()
        await ContactRepository.upsert(
            Contact.from_radio_dict(lower_key, contact_data, on_radio=True)
        )
        claimed = await MessageRepository.claim_prefix_messages(lower_key)
        if claimed > 0:
            logger.info("Claimed %d prefix DM message(s) for contact %s", claimed, public_key[:12])
        count += 1

    logger.info("Synced %d contacts from radio", count)
    return {"synced": count}


@router.post("/{public_key}/remove-from-radio")
async def remove_contact_from_radio(public_key: str) -> dict:
    """Remove a contact from the radio (keeps it in database)."""
    require_connected()

    contact = await _resolve_contact_or_404(public_key)

    async with radio_manager.radio_operation("remove_contact_from_radio") as mc:
        # Get the contact from radio
        radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if not radio_contact:
            # Already not on radio
            await ContactRepository.set_on_radio(contact.public_key, False)
            return {"status": "ok", "message": "Contact was not on radio"}

        logger.info("Removing contact %s from radio", contact.public_key[:12])

        result = await mc.commands.remove_contact(radio_contact)

        if result.type == EventType.ERROR:
            raise HTTPException(
                status_code=500, detail=f"Failed to remove contact: {result.payload}"
            )

    await ContactRepository.set_on_radio(contact.public_key, False)
    return {"status": "ok"}


@router.post("/{public_key}/add-to-radio")
async def add_contact_to_radio(public_key: str) -> dict:
    """Add a contact from the database to the radio."""
    require_connected()

    contact = await _resolve_contact_or_404(public_key, "Contact not found in database")

    async with radio_manager.radio_operation("add_contact_to_radio") as mc:
        # Check if already on radio
        radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if radio_contact:
            return {"status": "ok", "message": "Contact already on radio"}

        logger.info("Adding contact %s to radio", contact.public_key[:12])

        result = await mc.commands.add_contact(contact.to_radio_dict())

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to add contact: {result.payload}")

    await ContactRepository.set_on_radio(contact.public_key, True)
    return {"status": "ok"}


@router.post("/{public_key}/mark-read")
async def mark_contact_read(public_key: str) -> dict:
    """Mark a contact conversation as read (update last_read_at timestamp)."""
    contact = await _resolve_contact_or_404(public_key)

    updated = await ContactRepository.update_last_read_at(contact.public_key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update read state")

    return {"status": "ok", "public_key": contact.public_key}


@router.delete("/{public_key}")
async def delete_contact(public_key: str) -> dict:
    """Delete a contact from the database (and radio if present)."""
    contact = await _resolve_contact_or_404(public_key)

    # Remove from radio if connected and contact is on radio
    if radio_manager.is_connected:
        async with radio_manager.radio_operation("delete_contact_from_radio") as mc:
            radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
            if radio_contact:
                logger.info(
                    "Removing contact %s from radio before deletion", contact.public_key[:12]
                )
                await mc.commands.remove_contact(radio_contact)

    # Delete from database
    await ContactRepository.delete(contact.public_key)
    logger.info("Deleted contact %s", contact.public_key[:12])

    return {"status": "ok"}


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
    - clock, clock sync
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

        send_result = await mc.commands.send_cmd(contact.public_key, request.command)

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


@router.post("/{public_key}/trace", response_model=TraceResponse)
async def request_trace(public_key: str) -> TraceResponse:
    """Send a single-hop trace to a contact and wait for the result.

    The trace path contains the contact's 1-byte pubkey hash as the sole hop
    (no intermediate repeaters). The radio firmware requires at least one
    node in the path.
    """
    require_connected()

    contact = await _resolve_contact_or_404(public_key)

    tag = random.randint(1, 0xFFFFFFFF)
    # First 2 hex chars of pubkey = 1-byte hash used by the trace protocol
    contact_hash = contact.public_key[:2]

    # Trace does not need auto-fetch suspension: response arrives as TRACE_DATA
    # from the reader loop, not via get_msg().
    async with radio_manager.radio_operation("request_trace", pause_polling=True) as mc:
        # Ensure contact is on radio so the trace can reach them
        await _ensure_on_radio(mc, contact)

        logger.info(
            "Sending trace to %s (tag=%d, hash=%s)", contact.public_key[:12], tag, contact_hash
        )
        result = await mc.commands.send_trace(path=contact_hash, tag=tag)

        if result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to send trace: {result.payload}")

        # Wait for the matching TRACE_DATA event
        event = await mc.wait_for_event(
            EventType.TRACE_DATA,
            attribute_filters={"tag": tag},
            timeout=15,
        )

    if event is None:
        raise HTTPException(status_code=504, detail="No trace response heard")

    trace = event.payload
    path = trace.get("path", [])
    path_len = trace.get("path_len", 0)

    # remote_snr: first entry in path (what the target heard us at)
    remote_snr = path[0]["snr"] if path else None
    # local_snr: last entry in path (what we heard them at on the bounce-back)
    local_snr = path[-1]["snr"] if path else None

    logger.info(
        "Trace result for %s: path_len=%d, remote_snr=%s, local_snr=%s",
        contact.public_key[:12],
        path_len,
        remote_snr,
        local_snr,
    )

    return TraceResponse(remote_snr=remote_snr, local_snr=local_snr, path_len=path_len)
