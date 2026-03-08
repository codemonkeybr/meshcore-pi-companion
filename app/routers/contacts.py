import logging
import random

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import (
    Contact,
    ContactActiveRoom,
    ContactAdvertPath,
    ContactAdvertPathSummary,
    ContactDetail,
    CreateContactRequest,
    NearestRepeater,
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["contacts"])


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


async def _ensure_on_radio(mc, contact: Contact) -> None:
    """Add a contact to the radio for routing, raising 500 on failure."""
    add_result = await mc.commands.add_contact(contact.to_radio_dict())
    if add_result is not None and add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=500, detail=f"Failed to add contact to radio: {add_result.payload}"
        )


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
            refreshed = await ContactRepository.get_by_key(request.public_key)
            if refreshed is not None:
                existing = refreshed

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

    # Backfill sender_key on channel messages that match this contact's name
    if request.name:
        backfilled = await MessageRepository.backfill_channel_sender_key(lower_key, request.name)
        if backfilled > 0:
            logger.info(
                "Backfilled sender_key on %d channel message(s) for %s", backfilled, request.name
            )

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
        prefix = p.next_hop
        if prefix:
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

    synced_keys: list[str] = []
    for public_key, contact_data in contacts.items():
        lower_key = public_key.lower()
        await ContactRepository.upsert(
            Contact.from_radio_dict(lower_key, contact_data, on_radio=True)
        )
        synced_keys.append(lower_key)
        claimed = await MessageRepository.claim_prefix_messages(lower_key)
        if claimed > 0:
            logger.info("Claimed %d prefix DM message(s) for contact %s", claimed, public_key[:12])
        adv_name = contact_data.get("adv_name")
        if adv_name:
            backfilled = await MessageRepository.backfill_channel_sender_key(lower_key, adv_name)
            if backfilled > 0:
                logger.info(
                    "Backfilled sender_key on %d channel message(s) for %s",
                    backfilled,
                    adv_name,
                )
        count += 1

    # Clear on_radio for contacts not found on the radio
    await ContactRepository.clear_on_radio_except(synced_keys)

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

    from app.websocket import broadcast_event

    broadcast_event("contact_deleted", {"public_key": contact.public_key})

    return {"status": "ok"}


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


@router.post("/{public_key}/reset-path")
async def reset_contact_path(public_key: str) -> dict:
    """Reset a contact's routing path to flood."""
    contact = await _resolve_contact_or_404(public_key)

    await ContactRepository.update_path(contact.public_key, "", -1)
    logger.info("Reset path to flood for %s", contact.public_key[:12])

    # Push the updated path to radio if connected and contact is on radio
    if radio_manager.is_connected and contact.on_radio:
        try:
            updated = await ContactRepository.get_by_key(contact.public_key)
            if updated:
                async with radio_manager.radio_operation("reset_path_on_radio") as mc:
                    await mc.commands.add_contact(updated.to_radio_dict())
        except Exception:
            logger.warning("Failed to push flood path to radio for %s", contact.public_key[:12])

    # Broadcast updated contact so frontend refreshes
    from app.websocket import broadcast_event

    updated_contact = await ContactRepository.get_by_key(contact.public_key)
    if updated_contact:
        broadcast_event("contact", updated_contact.model_dump())

    return {"status": "ok", "public_key": contact.public_key}
