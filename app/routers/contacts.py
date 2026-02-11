import asyncio
import logging
import random

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from meshcore import EventType

from app.dependencies import require_connected
from app.models import (
    CONTACT_TYPE_REPEATER,
    AclEntry,
    CommandRequest,
    CommandResponse,
    Contact,
    CreateContactRequest,
    NeighborInfo,
    TelemetryRequest,
    TelemetryResponse,
    TraceResponse,
)
from app.packet_processor import start_historical_dm_decryption
from app.radio import radio_manager
from app.repository import ContactRepository, MessageRepository

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


async def prepare_repeater_connection(mc, contact: Contact, password: str) -> None:
    """Prepare connection to a repeater by adding to radio and logging in.

    Args:
        mc: MeshCore instance
        contact: The repeater contact
        password: Password for login (empty string for no password)

    Raises:
        HTTPException: If login fails
    """
    # Add contact to radio with path from DB
    logger.info("Adding repeater %s to radio", contact.public_key[:12])
    await mc.commands.add_contact(contact.to_radio_dict())

    # Send login with password
    logger.info("Sending login to repeater %s", contact.public_key[:12])
    login_result = await mc.commands.send_login(contact.public_key, password)

    if login_result.type == EventType.ERROR:
        raise HTTPException(status_code=401, detail=f"Login failed: {login_result.payload}")

    # Wait for key exchange to complete before sending requests
    logger.debug("Waiting %.1fs for key exchange to complete", REPEATER_OP_DELAY_SECONDS)
    await asyncio.sleep(REPEATER_OP_DELAY_SECONDS)


@router.get("", response_model=list[Contact])
async def list_contacts(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Contact]:
    """List contacts from the database."""
    return await ContactRepository.get_all(limit=limit, offset=offset)


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
    existing = await ContactRepository.get_by_key_or_prefix(request.public_key)
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


@router.get("/{public_key}", response_model=Contact)
async def get_contact(public_key: str) -> Contact:
    """Get a specific contact by public key or prefix."""
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.post("/sync")
async def sync_contacts_from_radio() -> dict:
    """Sync contacts from the radio to the database."""
    mc = require_connected()

    logger.info("Syncing contacts from radio")

    result = await mc.commands.get_contacts()

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to get contacts: {result.payload}")

    contacts = result.payload
    count = 0

    for public_key, contact_data in contacts.items():
        await ContactRepository.upsert(
            Contact.from_radio_dict(public_key, contact_data, on_radio=True)
        )
        count += 1

    logger.info("Synced %d contacts from radio", count)
    return {"synced": count}


@router.post("/{public_key}/remove-from-radio")
async def remove_contact_from_radio(public_key: str) -> dict:
    """Remove a contact from the radio (keeps it in database)."""
    mc = require_connected()

    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Get the contact from radio
    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
    if not radio_contact:
        # Already not on radio
        await ContactRepository.set_on_radio(contact.public_key, False)
        return {"status": "ok", "message": "Contact was not on radio"}

    logger.info("Removing contact %s from radio", contact.public_key[:12])

    result = await mc.commands.remove_contact(radio_contact)

    if result.type == EventType.ERROR:
        raise HTTPException(status_code=500, detail=f"Failed to remove contact: {result.payload}")

    await ContactRepository.set_on_radio(contact.public_key, False)
    return {"status": "ok"}


@router.post("/{public_key}/add-to-radio")
async def add_contact_to_radio(public_key: str) -> dict:
    """Add a contact from the database to the radio."""
    mc = require_connected()

    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found in database")

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
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    updated = await ContactRepository.update_last_read_at(contact.public_key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update read state")

    return {"status": "ok", "public_key": contact.public_key}


@router.delete("/{public_key}")
async def delete_contact(public_key: str) -> dict:
    """Delete a contact from the database (and radio if present)."""
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Remove from radio if connected and contact is on radio
    if radio_manager.is_connected and radio_manager.meshcore:
        mc = radio_manager.meshcore
        radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
        if radio_contact:
            logger.info("Removing contact %s from radio before deletion", contact.public_key[:12])
            await mc.commands.remove_contact(radio_contact)

    # Delete from database
    await ContactRepository.delete(contact.public_key)
    logger.info("Deleted contact %s", contact.public_key[:12])

    return {"status": "ok"}


@router.post("/{public_key}/telemetry", response_model=TelemetryResponse)
async def request_telemetry(public_key: str, request: TelemetryRequest) -> TelemetryResponse:
    """Request telemetry from a repeater.

    The contact must be a repeater (type=2). If not on the radio, it will be added.
    Uses login + status request with retry logic.
    """
    mc = require_connected()

    # Get contact from database
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Verify it's a repeater
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})",
        )

    async with radio_manager.radio_operation(
        "request_telemetry",
        meshcore=mc,
        pause_polling=True,
        suspend_auto_fetch=True,
    ):
        # Prepare connection (add/remove dance + login)
        await prepare_repeater_connection(mc, contact, request.password)

        # Request status with retries
        logger.info("Requesting status from repeater %s", contact.public_key[:12])
        status = None
        for attempt in range(1, 4):
            logger.debug("Status request attempt %d/3", attempt)
            status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)
            if status:
                break
            logger.debug("Status request timeout, retrying...")

        if not status:
            raise HTTPException(status_code=504, detail="No response from repeater after 3 attempts")

        logger.info("Received telemetry from %s: %s", contact.public_key[:12], status)

        # Fetch neighbors (fetch_all_neighbours handles pagination)
        logger.info("Fetching neighbors from repeater %s", contact.public_key[:12])
        neighbors_data = None
        for attempt in range(1, 4):
            logger.debug("Neighbors request attempt %d/3", attempt)
            neighbors_data = await mc.commands.fetch_all_neighbours(
                contact.public_key, timeout=10, min_timeout=5
            )
            if neighbors_data:
                break
            logger.debug("Neighbors request timeout, retrying...")

        # Process neighbors - resolve pubkey prefixes to contact names
        neighbors: list[NeighborInfo] = []
        if neighbors_data and "neighbours" in neighbors_data:
            logger.info("Received %d neighbors", len(neighbors_data["neighbours"]))
            for n in neighbors_data["neighbours"]:
                pubkey_prefix = n.get("pubkey", "")
                # Try to resolve to a contact name from our database
                resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
                neighbors.append(
                    NeighborInfo(
                        pubkey_prefix=pubkey_prefix,
                        name=resolved_contact.name if resolved_contact else None,
                        snr=n.get("snr", 0.0),
                        last_heard_seconds=n.get("secs_ago", 0),
                    )
                )

        # Fetch ACL
        logger.info("Fetching ACL from repeater %s", contact.public_key[:12])
        acl_data = None
        for attempt in range(1, 4):
            logger.debug("ACL request attempt %d/3", attempt)
            acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)
            if acl_data:
                break
            logger.debug("ACL request timeout, retrying...")

        # Process ACL - resolve pubkey prefixes to contact names
        acl_entries: list[AclEntry] = []
        if acl_data and isinstance(acl_data, list):
            logger.info("Received %d ACL entries", len(acl_data))
            for entry in acl_data:
                pubkey_prefix = entry.get("key", "")
                perm = entry.get("perm", 0)
                # Try to resolve to a contact name from our database
                resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
                acl_entries.append(
                    AclEntry(
                        pubkey_prefix=pubkey_prefix,
                        name=resolved_contact.name if resolved_contact else None,
                        permission=perm,
                        permission_name=ACL_PERMISSION_NAMES.get(perm, f"Unknown({perm})"),
                    )
                )

        # Fetch clock output (up to 2 attempts)
        logger.info("Fetching clock from repeater %s", contact.public_key[:12])
        clock_output: str | None = None
        for attempt in range(1, 3):
            logger.debug("Clock request attempt %d/2", attempt)
            try:
                send_result = await mc.commands.send_cmd(contact.public_key, "clock")
                if send_result.type == EventType.ERROR:
                    logger.debug("Clock command send error: %s", send_result.payload)
                    continue

                # Wait for response
                wait_result = await mc.wait_for_event(EventType.MESSAGES_WAITING, timeout=5.0)
                if wait_result is None:
                    logger.debug("Clock request timeout, retrying...")
                    continue

                response_event = await mc.commands.get_msg()
                if response_event.type == EventType.ERROR:
                    logger.debug("Clock get_msg error: %s", response_event.payload)
                    continue

                clock_output = response_event.payload.get("text", "")
                logger.info("Received clock output: %s", clock_output)
                break
            except Exception as e:
                logger.debug("Clock request exception: %s", e)
                continue

    if clock_output is None:
        clock_output = "Unable to fetch `clock` output (repeater did not respond)"

    # Convert raw telemetry to response format
    # bat is in mV, convert to V (e.g., 3775 -> 3.775)

    return TelemetryResponse(
        pubkey_prefix=status.get("pubkey_pre", contact.public_key[:12]),
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
        neighbors=neighbors,
        acl=acl_entries,
        clock_output=clock_output,
    )


@router.post("/{public_key}/command", response_model=CommandResponse)
async def send_repeater_command(public_key: str, request: CommandRequest) -> CommandResponse:
    """Send a CLI command to a repeater.

    The contact must be a repeater (type=2). The user must have already logged in
    via the telemetry endpoint. This endpoint ensures the contact is on the radio
    before sending commands (the repeater remembers ACL permissions after login).

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
    mc = require_connected()

    # Get contact from database
    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Verify it's a repeater
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})",
        )

    async with radio_manager.radio_operation(
        "send_repeater_command",
        meshcore=mc,
        pause_polling=True,
        suspend_auto_fetch=True,
    ):
        # Add contact to radio with path from DB
        logger.info("Adding repeater %s to radio", contact.public_key[:12])
        await mc.commands.add_contact(contact.to_radio_dict())

        # Send the command
        logger.info("Sending command to repeater %s: %s", contact.public_key[:12], request.command)

        send_result = await mc.commands.send_cmd(contact.public_key, request.command)

        if send_result.type == EventType.ERROR:
            raise HTTPException(status_code=500, detail=f"Failed to send command: {send_result.payload}")

        # Wait for response (MESSAGES_WAITING event, then get_msg)
        try:
            wait_result = await mc.wait_for_event(EventType.MESSAGES_WAITING, timeout=10.0)

            if wait_result is None:
                # Timeout - no response received
                logger.warning(
                    "No response from repeater %s for command: %s",
                    contact.public_key[:12],
                    request.command,
                )
                return CommandResponse(
                    command=request.command,
                    response="(no response - command may have been processed)",
                )

            response_event = await mc.commands.get_msg()

            if response_event.type == EventType.ERROR:
                return CommandResponse(command=request.command, response=f"(error: {response_event.payload})")

            # Extract the response text and timestamp from the payload
            response_text = response_event.payload.get("text", str(response_event.payload))
            sender_timestamp = response_event.payload.get("timestamp")
            logger.info("Received response from %s: %s", contact.public_key[:12], response_text)

            return CommandResponse(
                command=request.command,
                response=response_text,
                sender_timestamp=sender_timestamp,
            )
        except Exception as e:
            logger.error("Error waiting for response: %s", e)
            return CommandResponse(
                command=request.command, response=f"(error waiting for response: {e})"
            )


@router.post("/{public_key}/trace", response_model=TraceResponse)
async def request_trace(public_key: str) -> TraceResponse:
    """Send a single-hop trace to a contact and wait for the result.

    The trace path contains the contact's 1-byte pubkey hash as the sole hop
    (no intermediate repeaters). The radio firmware requires at least one
    node in the path.
    """
    mc = require_connected()

    contact = await ContactRepository.get_by_key_or_prefix(public_key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    tag = random.randint(1, 0xFFFFFFFF)
    # First 2 hex chars of pubkey = 1-byte hash used by the trace protocol
    contact_hash = contact.public_key[:2]

    # Trace does not need auto-fetch suspension: response arrives as TRACE_DATA
    # from the reader loop, not via get_msg().
    async with radio_manager.radio_operation("request_trace", pause_polling=True):
        # Ensure contact is on radio so the trace can reach them
        await mc.commands.add_contact(contact.to_radio_dict())

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
