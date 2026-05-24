"""
Radio sync and offload management.

This module handles syncing contacts and channels from the radio to the database,
then removing them from the radio to free up space for new discoveries.

Also handles loading favorites plus recently active contacts TO the radio for DM ACK support.
Also handles periodic message polling as a fallback for platforms where push events
don't work reliably.
"""

import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from meshcore import EventType

from app.config import settings
from app.event_handlers import cleanup_expired_acks
from app.models import _VALID_CONTACT_TYPES, Contact, ContactUpsert
from app.radio import RadioOperationBusyError
from app.radio_backend import RadioBackend
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    RepeaterTelemetryRepository,
)
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    reconcile_contact_messages,
)
from app.services.messages import create_fallback_channel_message
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHANNELS = 40

# Radio autoadd config bit: overwrite oldest contact when table is full
_AUTO_ADD_OVERWRITE_OLDEST = 0x01

# Radio contact favorite bit (matches MeshCore firmware)
_RADIO_CONTACT_FAVORITE = 0x01

# Background contact reconcile tuning
CONTACT_RECONCILE_BATCH_SIZE = 2
CONTACT_RECONCILE_YIELD_SECONDS = 0.05
CONTACT_RECONCILE_BUSY_BACKOFF_SECONDS = 2.0


def _to_backend(mc_or_be):
    """Wrap raw MeshCore in ClientBackend; pass through RadioBackend as-is."""
    if isinstance(mc_or_be, RadioBackend):
        return mc_or_be
    from app.backends.client_backend import ClientBackend

    return ClientBackend(mc_or_be)


def _raw_mc(be):
    """Extract raw MeshCore from RadioBackend; return as-is if not a RadioBackend.

    Uses isinstance so MagicMock test objects are NOT unwrapped (they have no _mc).
    """
    if isinstance(be, RadioBackend):
        return getattr(be, "_mc", be)
    return be


async def _enable_autoevict_on_radio(mc) -> bool:
    """Ensure the radio's AUTO_ADD_OVERWRITE_OLDEST preference bit is set."""
    try:
        current = await mc.commands.get_autoadd_config()
        if current is None or current.type == EventType.ERROR:
            logger.warning("Could not read autoadd config from radio: %s", current)
            return False
        current_flags = current.payload.get("config", 0)
        if current_flags & _AUTO_ADD_OVERWRITE_OLDEST:
            logger.debug("Radio autoevict already enabled (autoadd_config=0x%02x)", current_flags)
            return True
        new_flags = current_flags | _AUTO_ADD_OVERWRITE_OLDEST
        result = await mc.commands.set_autoadd_config(new_flags)
        if result is not None and result.type == EventType.OK:
            logger.info(
                "Enabled radio autoevict (autoadd_config 0x%02x -> 0x%02x)",
                current_flags,
                new_flags,
            )
            return True
        else:
            logger.warning("Failed to enable radio autoevict: %s", result)
            return False
    except Exception as exc:
        logger.warning("Error enabling radio autoevict: %s", exc)
        return False


def _contact_sync_debug_fields(contact: Contact) -> dict[str, object]:
    """Return key contact fields for sync failure diagnostics."""
    return {
        "type": contact.type,
        "flags": contact.flags,
        "last_path": getattr(contact, "last_path", contact.direct_path),
        "last_path_len": getattr(contact, "last_path_len", contact.direct_path_len),
        "out_path_hash_mode": getattr(contact, "out_path_hash_mode", contact.direct_path_hash_mode),
        "route_override_path": contact.route_override_path,
        "route_override_len": contact.route_override_len,
        "route_override_hash_mode": contact.route_override_hash_mode,
        "last_advert": contact.last_advert,
        "lat": contact.lat,
        "lon": contact.lon,
    }


async def _reconcile_contact_messages_background(
    public_key: str,
    contact_name: str | None,
) -> None:
    """Run prefix promotion and contact/message reconciliation outside the radio critical path."""
    try:
        promoted_keys = await promote_prefix_contacts_for_contact(
            public_key=public_key,
            log=logger,
        )
        await reconcile_contact_messages(
            public_key=public_key,
            contact_name=contact_name,
            log=logger,
        )
        if promoted_keys:
            contact = await ContactRepository.get_by_key(public_key.lower())
            if contact is not None:
                for old_key in promoted_keys:
                    broadcast_event(
                        "contact_resolved",
                        {"previous_public_key": old_key, "contact": contact.model_dump()},
                    )
    except Exception as exc:
        logger.warning(
            "Background contact reconciliation failed for %s: %s",
            public_key[:12],
            exc,
            exc_info=True,
        )


async def upsert_channel_from_radio_slot(payload: dict, *, on_radio: bool) -> str | None:
    """Parse a radio channel-slot payload and upsert to the database.

    Returns the uppercase hex key if a channel was upserted, or None if the
    slot was empty/invalid.
    """
    name = payload.get("channel_name", "")
    secret = payload.get("channel_secret", b"")

    # Skip empty channels
    if not name or name == "\x00" * len(name):
        return None

    is_hashtag = name.startswith("#")
    key_bytes = secret if isinstance(secret, bytes) else bytes(secret)
    key_hex = key_bytes.hex().upper()

    await ChannelRepository.upsert(
        key=key_hex,
        name=name,
        is_hashtag=is_hashtag,
        on_radio=on_radio,
    )
    return key_hex


def get_radio_channel_limit(max_channels: int | None = None) -> int:
    """Return the effective channel-slot limit for the connected firmware."""
    discovered = getattr(radio_manager, "max_channels", DEFAULT_MAX_CHANNELS)
    try:
        limit = max(1, int(discovered))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_CHANNELS

    if max_channels is not None:
        return min(limit, max(1, int(max_channels)))

    return limit


# Message poll task handle
_message_poll_task: asyncio.Task | None = None

# Message poll interval in seconds when aggressive fallback is enabled.
MESSAGE_POLL_INTERVAL = 10

# Always-on audit interval when aggressive fallback is disabled.
MESSAGE_POLL_AUDIT_INTERVAL = 3600

# Periodic advertisement task handle
_advert_task: asyncio.Task | None = None

# Telemetry collection task handle
_telemetry_collect_task: asyncio.Task | None = None

# Default check interval when periodic advertising is disabled (seconds)
# We still need to periodically check if it's been enabled
ADVERT_CHECK_INTERVAL = 60

# Minimum allowed advertisement interval (1 hour).
# Even if the database has a shorter value, we silently refuse to advertise
# more frequently than this.
MIN_ADVERT_INTERVAL = 3600

# Counter to pause polling during repeater operations (supports nested pauses)
_polling_pause_count: int = 0


def is_polling_paused() -> bool:
    """Check if polling is currently paused."""
    return _polling_pause_count > 0


@asynccontextmanager
async def pause_polling():
    """Context manager to pause message polling during repeater operations.

    Supports nested pauses - polling only resumes when all pause contexts have exited.
    """
    global _polling_pause_count
    _polling_pause_count += 1
    try:
        yield
    finally:
        _polling_pause_count -= 1


# Background task handle
_sync_task: asyncio.Task | None = None

# Startup/background contact reconciliation task handle
_contact_reconcile_task: asyncio.Task | None = None

# Periodic maintenance check interval in seconds (5 minutes)
SYNC_INTERVAL = 300

# Reload non-favorite contacts up to 80% of configured radio capacity after offload.
RADIO_CONTACT_REFILL_RATIO = 0.80

# Trigger a full offload/reload once occupancy reaches 95% of configured capacity.
RADIO_CONTACT_FULL_SYNC_RATIO = 0.95


def _effective_radio_capacity(configured: int) -> int:
    """Return the effective radio contact capacity (configured capped by hardware limit)."""
    capacity = max(1, configured)
    hw_limit = radio_manager.max_contacts
    if hw_limit is not None:
        capacity = min(capacity, hw_limit)
    return max(1, capacity)


def _compute_radio_contact_limits(max_contacts: int) -> tuple[int, int]:
    """Return (refill_target, full_sync_trigger) for the configured capacity."""
    capacity = max(1, max_contacts)
    refill_target = max(1, min(capacity, int((capacity * RADIO_CONTACT_REFILL_RATIO) + 0.5)))
    full_sync_trigger = max(
        refill_target,
        min(capacity, math.ceil(capacity * RADIO_CONTACT_FULL_SYNC_RATIO)),
    )
    return refill_target, full_sync_trigger


async def should_run_full_periodic_sync(mc_or_be) -> bool:
    """Check current radio occupancy and decide whether to offload/reload."""
    be = _to_backend(mc_or_be)
    app_settings = await AppSettingsRepository.get()
    capacity = _effective_radio_capacity(app_settings.max_radio_contacts)
    refill_target, full_sync_trigger = _compute_radio_contact_limits(capacity)

    result = await be.get_contacts()
    if result is None or result.type == EventType.ERROR:
        logger.warning("Periodic sync occupancy check failed: %s", result)
        return False

    current_contacts = len(result.payload or {})
    if current_contacts >= full_sync_trigger:
        logger.info(
            "Running full radio sync: %d/%d contacts on radio (trigger=%d, refill_target=%d)",
            current_contacts,
            capacity,
            full_sync_trigger,
            refill_target,
        )
        return True

    logger.debug(
        "Skipping full radio sync: %d/%d contacts on radio (trigger=%d, refill_target=%d)",
        current_contacts,
        capacity,
        full_sync_trigger,
        refill_target,
    )
    return False


async def sync_and_offload_contacts(be: RadioBackend) -> dict:
    """
    Sync contacts from radio to database, then remove them from radio.
    Returns counts of synced and removed contacts.
    """
    synced = 0
    removed = 0

    try:
        # Get all contacts from radio
        result = await be.get_contacts()

        if result is None or result.type == EventType.ERROR:
            logger.error(
                "Failed to get contacts from radio: %s. "
                "If you see this repeatedly, the radio may be visible on the "
                "serial/TCP/BLE port but not responding to commands. Check for "
                "another process with the serial port open (other RemoteTerm "
                "instances, serial monitors, etc.), verify the firmware is "
                "up-to-date and in client mode (not repeater), or try a "
                "power cycle.",
                result,
            )
            return {"synced": 0, "removed": 0, "error": str(result)}

        contacts = result.payload or {}
        logger.info("Found %d contacts on radio", len(contacts))

        # Sync each contact to database, then remove from radio
        for public_key, contact_data in contacts.items():
            # Save to database
            await ContactRepository.upsert(
                ContactUpsert.from_radio_dict(public_key, contact_data, on_radio=False)
            )
            asyncio.create_task(
                _reconcile_contact_messages_background(
                    public_key,
                    contact_data.get("adv_name"),
                )
            )
            synced += 1

            # Remove from radio
            try:
                remove_result = await be.remove_contact(contact_data)
                if remove_result.type == EventType.OK:
                    removed += 1

                    # LIBRARY INTERNAL FIXUP: The MeshCore library's
                    # commands.remove_contact() sends the remove command over
                    # the wire but does NOT update the library's in-memory
                    # contact cache (mc._contacts). This is a gap in the
                    # library — there's no public API to clear a single
                    # contact from the cache, and the library only refreshes
                    # it on a full get_contacts() call.
                    #
                    # Why this matters: sync_recent_contacts_to_radio() uses
                    # get_contact_by_key_prefix() to check whether a
                    # contact is already loaded on the radio. That method
                    # searches the cache. If we don't evict the removed
                    # contact from the cache here, get_contact_by_key_prefix()
                    # will still find it and skip the add_contact() call —
                    # meaning contacts never get loaded back onto the radio
                    # after offload. The result: no DM ACKs, degraded routing
                    # for potentially minutes until the next periodic sync
                    # refreshes the cache from the (now-empty) radio.
                    be.evict_contact_from_cache(public_key)
                else:
                    logger.warning(
                        "Failed to remove contact %s: %s", public_key[:12], remove_result.payload
                    )
            except Exception as e:
                logger.warning("Error removing contact %s: %s", public_key[:12], e)

        logger.info("Synced %d contacts, removed %d from radio", synced, removed)

    except Exception as e:
        logger.error("Error during contact sync: %s", e)
        return {"synced": synced, "removed": removed, "error": str(e)}

    return {"synced": synced, "removed": removed}


async def sync_contacts_from_radio(mc_or_be) -> dict:
    """Pull contacts from the radio and persist them (without removing from radio).

    Returns dict with ``radio_contacts`` (keyed by public key) for use by the
    background contact reconcile loop.
    """
    be = _to_backend(mc_or_be)
    synced = 0

    try:
        result = await be.get_contacts()

        if result is None or result.type == EventType.ERROR:
            logger.error(
                "Failed to get contacts from radio: %s. "
                "If you see this repeatedly, the radio may be visible on the "
                "serial/TCP/BLE port but not responding to commands. Check for "
                "another process with the serial port open (other RemoteTerm "
                "instances, serial monitors, etc.), verify the firmware is "
                "up-to-date and in client mode (not repeater), or try a "
                "power cycle.",
                result,
            )
            return {"synced": 0, "radio_contacts": {}, "error": str(result)}

        contacts = _normalize_radio_contacts_payload(result.payload)
        logger.debug("Found %d contacts on radio", len(contacts))

        for public_key, contact_data in contacts.items():
            await ContactRepository.upsert(
                ContactUpsert.from_radio_dict(public_key, contact_data, on_radio=False)
            )
            asyncio.create_task(
                _reconcile_contact_messages_background(
                    public_key,
                    contact_data.get("adv_name"),
                )
            )
            synced += 1

        logger.debug("Synced %d contacts from radio snapshot", synced)

        radio_fav_keys = [
            pk
            for pk, data in contacts.items()
            if data.get("flags", 0) & 0x01 and data.get("type", -1) in _VALID_CONTACT_TYPES
        ]
        if radio_fav_keys:
            try:
                imported = 0
                for pk in radio_fav_keys:
                    existing = await ContactRepository.get_by_key(pk)
                    if existing and not existing.favorite:
                        await ContactRepository.set_favorite(pk, True)
                        imported += 1
                if imported:
                    logger.info("Imported %d radio favorite(s) into app favorites", imported)
            except Exception as e:
                logger.warning("Failed to import radio favorites: %s", e)

        return {"synced": synced, "radio_contacts": contacts}
    except Exception as e:
        logger.error("Error during contact snapshot sync: %s", e)
        return {"synced": synced, "radio_contacts": {}, "error": str(e)}


def _normalize_radio_contacts_payload(contacts: dict | None) -> dict[str, dict]:
    """Return radio contacts keyed by normalized lowercase full public key."""
    normalized: dict[str, dict] = {}
    for public_key, contact_data in (contacts or {}).items():
        normalized[str(public_key).lower()] = contact_data
    return normalized


async def sync_and_offload_channels(mc_or_be, max_channels: int | None = None) -> dict:
    """
    Sync channels from radio to database, then clear them from radio.
    Returns counts of synced and cleared channels.
    """
    be = _to_backend(mc_or_be)
    synced = 0
    cleared = 0

    try:
        radio_manager.reset_channel_send_cache()
        channel_limit = get_radio_channel_limit(max_channels)

        # Check all available channel slots for this firmware variant
        for idx in range(channel_limit):
            result = await be.get_channel(idx)

            if result.type != EventType.CHANNEL_INFO:
                continue

            key_hex = await upsert_channel_from_radio_slot(
                result.payload,
                on_radio=False,  # We're about to clear it
            )
            if key_hex is None:
                continue

            radio_manager.remember_pending_message_channel_slot(key_hex, idx)
            synced += 1
            logger.debug("Synced channel %s: %s", key_hex[:8], result.payload.get("channel_name"))

            # Clear from radio (set empty name and zero key)
            try:
                clear_result = await be.set_channel(
                    channel_idx=idx,
                    channel_name="",
                    channel_secret=bytes(16),
                )
                if clear_result.type == EventType.OK:
                    cleared += 1
                else:
                    logger.warning("Failed to clear channel %d: %s", idx, clear_result.payload)
            except Exception as e:
                logger.warning("Error clearing channel %d: %s", idx, e)

        logger.info("Synced %d channels, cleared %d from radio", synced, cleared)

    except Exception as e:
        logger.error("Error during channel sync: %s", e)
        return {"synced": synced, "cleared": cleared, "error": str(e)}

    return {"synced": synced, "cleared": cleared}


def _split_channel_sender_and_text(text: str) -> tuple[str | None, str]:
    """Parse the canonical MeshCore "<sender>: <message>" channel text format."""
    sender = None
    message_text = text
    colon_idx = text.find(": ")
    if 0 < colon_idx < 50:
        potential_sender = text[:colon_idx]
        if not any(char in potential_sender for char in ":[]\x00"):
            sender = potential_sender
            message_text = text[colon_idx + 2 :]
    return sender, message_text


async def _resolve_channel_for_pending_message(
    be: RadioBackend,
    channel_idx: int,
) -> tuple[str | None, str | None]:
    """Resolve a pending channel message's slot to a channel key and name."""
    try:
        result = await be.get_channel(channel_idx)
    except Exception as exc:
        logger.debug("Failed to fetch channel slot %s for pending message: %s", channel_idx, exc)
    else:
        if result.type == EventType.CHANNEL_INFO:
            key_hex = await upsert_channel_from_radio_slot(result.payload, on_radio=False)
            if key_hex is not None:
                radio_manager.remember_pending_message_channel_slot(key_hex, channel_idx)
                return key_hex, result.payload.get("channel_name") or None

    current_slot_map = getattr(radio_manager, "_channel_key_by_slot", {})
    cached_key = current_slot_map.get(channel_idx)
    if cached_key is None:
        cached_key = radio_manager.get_pending_message_channel_key(channel_idx)
    if cached_key is None:
        return None, None

    channel = await ChannelRepository.get_by_key(cached_key)
    return cached_key, channel.name if channel else None


async def _store_pending_direct_message(event) -> None:
    """Route a CONTACT_MSG_RECV event pulled via get_msg() through the DM ingest path."""
    from app.event_handlers import on_contact_message

    try:
        await on_contact_message(event)
    except Exception:
        logger.warning("Failed to store pending direct message", exc_info=True)


async def _store_pending_channel_message(mc_or_be, payload: dict) -> None:
    """Persist a CHANNEL_MSG_RECV event pulled via get_msg()."""
    be = _to_backend(mc_or_be)
    channel_idx = payload.get("channel_idx")
    if channel_idx is None:
        logger.warning("Pending channel message missing channel_idx; dropping payload")
        return

    try:
        normalized_channel_idx = int(channel_idx)
    except (TypeError, ValueError):
        logger.warning("Pending channel message had invalid channel_idx=%r", channel_idx)
        return

    channel_key, channel_name = await _resolve_channel_for_pending_message(
        be, normalized_channel_idx
    )
    if channel_key is None:
        logger.warning(
            "Could not resolve channel slot %d for pending message; message cannot be stored",
            normalized_channel_idx,
        )
        return

    received_at = int(time.time())
    sender_timestamp = payload.get("sender_timestamp") or received_at
    sender_name, message_text = _split_channel_sender_and_text(payload.get("text", ""))

    await create_fallback_channel_message(
        conversation_key=channel_key,
        message_text=message_text,
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=payload.get("path"),
        path_len=payload.get("path_len"),
        txt_type=payload.get("txt_type", 0),
        sender_name=sender_name,
        channel_name=channel_name,
        broadcast_fn=broadcast_event,
    )


async def ensure_default_channels() -> None:
    """
    Ensure default channels exist in the database.
    These will be configured on the radio when needed for sending.

    This seeds the canonical Public channel row in the database if it is missing
    or misnamed. It does not make the channel undeletable through the router.
    """
    # Public channel - no hashtag, specific well-known key
    PUBLIC_CHANNEL_KEY_HEX = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

    # Check by KEY (not name) since that's what's fixed
    existing = await ChannelRepository.get_by_key(PUBLIC_CHANNEL_KEY_HEX)
    if not existing or existing.name != "Public":
        logger.info("Ensuring default Public channel exists with correct name")
        await ChannelRepository.upsert(
            key=PUBLIC_CHANNEL_KEY_HEX,
            name="Public",
            is_hashtag=False,
            on_radio=existing.on_radio if existing else False,
        )


async def sync_and_offload_all(mc_or_be) -> dict:
    """Run fast startup sync, then background contact reconcile."""
    logger.info("Starting full radio sync and offload")
    autoevict_requested = settings.load_with_autoevict
    autoevict = False

    raw_mc = _raw_mc(_to_backend(mc_or_be))

    if autoevict_requested:
        autoevict = await _enable_autoevict_on_radio(raw_mc)
        if not autoevict:
            logger.warning(
                "Autoevict requested but unavailable; falling back to snapshot-based "
                "background contact reconcile"
            )

    # Contact on_radio is legacy/stale metadata. Clear it during the offload/reload
    # cycle so old rows stop claiming radio residency we do not actively track.
    await ContactRepository.clear_on_radio_except([])

    contacts_result = await sync_contacts_from_radio(mc_or_be)
    channels_result = await sync_and_offload_channels(mc_or_be)

    # Ensure default channels exist
    await ensure_default_channels()

    snapshot_failed = "error" in contacts_result
    if snapshot_failed and not autoevict:
        logger.warning(
            "Radio contact snapshot failed — attempting best-effort contact "
            "loading without a full picture of what's already on the radio"
        )
        broadcast_error(
            "Could not enumerate radio contacts",
            "Loading favorites and recent contacts on a best-effort basis — "
            "some adds may be redundant or fail if the radio's contact table "
            "is already full. Set MESHCORE_LOAD_WITH_AUTOEVICT=true for more "
            "reliable loading without needing to read the radio first. "
            "See 'Contact Loading Issues' in the Advanced Setup documentation.",
        )

    start_background_contact_reconciliation(
        initial_radio_contacts=contacts_result.get("radio_contacts", {}),
        expected_mc=raw_mc,
        autoevict=autoevict,
    )

    return {
        "contacts": contacts_result,
        "channels": channels_result,
        "contact_reconcile_started": True,
    }


async def drain_pending_messages(mc_or_be) -> int:
    """
    Drain all pending messages from the radio.

    Calls get_msg() repeatedly until NO_MORE_MSGS is received.
    Returns the count of messages retrieved.
    """
    be = _to_backend(mc_or_be)
    count = 0
    max_iterations = 100  # Safety limit

    for _ in range(max_iterations):
        try:
            result = await be.get_msg(timeout=2.0)

            if result.type == EventType.NO_MORE_MSGS:
                break
            elif result.type == EventType.ERROR:
                logger.debug("Error during message drain: %s", result.payload)
                break
            elif result.type in (EventType.CONTACT_MSG_RECV, EventType.CHANNEL_MSG_RECV):
                if result.type == EventType.CHANNEL_MSG_RECV:
                    await _store_pending_channel_message(mc_or_be, result.payload)
                count += 1

            # Small delay between fetches
            await asyncio.sleep(0.1)

        except TimeoutError:
            break
        except Exception as e:
            logger.warning("Error draining messages: %s", e, exc_info=True)
            break

    return count


async def poll_for_messages(mc_or_be) -> int:
    """
    Poll the radio for any pending messages (single pass).

    This is a fallback for platforms where MESSAGES_WAITING push events
    don't work reliably.

    Returns the count of messages retrieved.
    """
    be = _to_backend(mc_or_be)
    count = 0

    try:
        result = await be.get_msg(timeout=2.0)

        if result.type == EventType.NO_MORE_MSGS or result.type == EventType.ERROR:
            return 0
        elif result.type in (EventType.CONTACT_MSG_RECV, EventType.CHANNEL_MSG_RECV):
            if result.type == EventType.CHANNEL_MSG_RECV:
                await _store_pending_channel_message(be, result.payload)
            count += 1
            count += await drain_pending_messages(be)

    except TimeoutError:
        pass
    except Exception as e:
        logger.warning("Message poll exception: %s", e, exc_info=True)

    return count


def _normalize_channel_secret(payload: dict) -> bytes:
    """Return a normalized bytes representation of a radio channel secret."""
    secret = payload.get("channel_secret", b"")
    if isinstance(secret, bytes):
        return secret
    return bytes(secret)


async def audit_channel_send_cache(mc_or_be) -> bool:
    """Verify cached send-slot expectations still match radio channel contents.

    If a mismatch is detected, the app's send-slot cache is reset so future sends
    fall back to reloading channels before reuse resumes.
    """
    be = _to_backend(mc_or_be)
    if not radio_manager.channel_slot_reuse_enabled():
        return True

    cached_slots = radio_manager.get_channel_send_cache_snapshot()
    if not cached_slots:
        return True

    mismatches: list[str] = []
    for channel_key, slot in cached_slots:
        result = await be.get_channel(slot)
        if result.type != EventType.CHANNEL_INFO:
            mismatches.append(
                f"slot {slot}: expected {channel_key[:8]} but radio returned {result.type}"
            )
            continue

        observed_name = result.payload.get("channel_name") or ""
        observed_key = _normalize_channel_secret(result.payload).hex().upper()
        expected_channel = await ChannelRepository.get_by_key(channel_key)
        expected_name = expected_channel.name if expected_channel is not None else None

        if observed_key != channel_key or expected_name is None or observed_name != expected_name:
            mismatches.append(
                f"slot {slot}: expected {expected_name or '(missing db row)'} "
                f"{channel_key[:8]}, got {observed_name or '(empty)'} {observed_key[:8]}"
            )

    if not mismatches:
        return True

    logger.error(
        "[RADIO SYNC ERROR] A periodic radio audit discovered that the channel send-slot cache fell out of sync with radio state. This indicates that some other system, internal or external to the radio, has updated the channel slots on the radio (which the app assumes it has exclusive rights to, except on TCP-linked devices). The cache is resetting now, but you should review the README.md and consider using the environment variable MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true to make the radio use non-optimistic channel management and force-write the channel to radio before each send. This is a minor performance hit, but guarantees consistency. Mismatches found: %s",
        "; ".join(mismatches),
    )
    radio_manager.reset_channel_send_cache()
    broadcast_error(
        "A periodic poll task has discovered radio inconsistencies.",
        "Please check the logs for recommendations (search "
        "'MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE').",
    )
    return False


async def _message_poll_loop():
    """Background task that periodically polls for messages."""
    while True:
        try:
            aggressive_fallback = settings.enable_message_poll_fallback
            await asyncio.sleep(
                MESSAGE_POLL_INTERVAL if aggressive_fallback else MESSAGE_POLL_AUDIT_INTERVAL
            )

            if radio_manager.is_connected and not is_polling_paused():
                try:
                    async with radio_manager.radio_operation(
                        "message_poll_loop",
                        blocking=False,
                        suspend_auto_fetch=True,
                    ) as be:
                        count = await poll_for_messages(be)
                        await audit_channel_send_cache(be)
                        if count > 0:
                            if aggressive_fallback:
                                logger.warning(
                                    "Poll loop caught %d message(s) missed by auto-fetch",
                                    count,
                                )
                            else:
                                logger.error(
                                    "[RADIO SYNC ERROR] Periodic radio audit caught %d message(s) that were not "
                                    "surfaced via event subscription. This means that the method of event (new contacts, messages, etc.) awareness we want isn't giving us everything. There is a fallback method available; see README.md and consider "
                                    "setting MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true to "
                                    "enable active radio polling every few seconds.",
                                    count,
                                )
                                broadcast_error(
                                    "A periodic poll task has discovered radio inconsistencies.",
                                    "Please check the logs for recommendations (search "
                                    "'MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK').",
                                )
                except RadioOperationBusyError:
                    logger.debug("Skipping message poll: radio busy")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Error in message poll loop: %s", e, exc_info=True)


def start_message_polling():
    """Start the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task is None or _message_poll_task.done():
        _message_poll_task = asyncio.create_task(_message_poll_loop())
        if settings.enable_message_poll_fallback:
            logger.info(
                "Started periodic message polling task (aggressive fallback, interval: %ds)",
                MESSAGE_POLL_INTERVAL,
            )
        else:
            logger.info(
                "Started periodic message audit task (interval: %ds)",
                MESSAGE_POLL_AUDIT_INTERVAL,
            )


async def stop_message_polling():
    """Stop the periodic message polling background task."""
    global _message_poll_task
    if _message_poll_task and not _message_poll_task.done():
        _message_poll_task.cancel()
        try:
            await _message_poll_task
        except asyncio.CancelledError:
            pass
        _message_poll_task = None
        logger.info("Stopped periodic message polling")


async def send_advertisement(mc_or_be, *, force: bool = False, mode: str = "flood") -> bool:
    """Send an advertisement to announce presence on the mesh.

    Respects the configured advert_interval - won't send if not enough time
    has elapsed since the last advertisement, unless force=True.

    Args:
        be: The RadioBackend instance to use for the advertisement.
        force: If True, send immediately regardless of interval.
        mode: Advertisement mode ("flood" or "zero_hop").

    Returns True if successful, False otherwise (including if throttled).
    """
    be = _to_backend(mc_or_be)
    use_flood = mode == "flood"

    # Only flood adverts currently participate in persisted throttle state.
    if use_flood and not force:
        settings = await AppSettingsRepository.get()
        interval = settings.advert_interval
        last_time = settings.last_advert_time
        now = int(time.time())

        # If interval is 0, advertising is disabled
        if interval <= 0:
            logger.debug("Advertisement skipped: periodic advertising is disabled")
            return False

        # Enforce minimum interval floor
        interval = max(interval, MIN_ADVERT_INTERVAL)

        # Check if enough time has passed
        elapsed = now - last_time
        if elapsed < interval:
            remaining = interval - elapsed
            logger.debug(
                "Advertisement throttled: %d seconds remaining (interval=%d, elapsed=%d)",
                remaining,
                interval,
                elapsed,
            )
            return False

    try:
        result = await be.send_advert(flood=use_flood)
        if result.type == EventType.OK:
            if use_flood:
                now = int(time.time())
                await AppSettingsRepository.update(last_advert_time=now)
            logger.info("Advertisement sent successfully (%s)", mode)
            return True
        else:
            logger.warning("Failed to send advertisement: %s", result.payload)
            return False
    except Exception as e:
        logger.warning("Error sending advertisement: %s", e, exc_info=True)
        return False


async def _periodic_advert_loop():
    """Background task that periodically checks if an advertisement should be sent.

    The actual throttling logic is in send_advertisement(), which checks
    last_advert_time from the database. This loop just triggers the check
    periodically and sleeps between attempts.
    """
    while True:
        try:
            await asyncio.sleep(ADVERT_CHECK_INTERVAL)

            # Try to send - send_advertisement() handles all checks
            # (disabled, throttled, not connected)
            if radio_manager.is_connected:
                try:
                    async with radio_manager.radio_operation(
                        "periodic_advertisement",
                        blocking=False,
                    ) as be:
                        await send_advertisement(be)
                except RadioOperationBusyError:
                    logger.debug("Skipping periodic advertisement: radio busy")

        except asyncio.CancelledError:
            logger.info("Periodic advertisement task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic advertisement loop: %s", e, exc_info=True)


def start_periodic_advert():
    """Start the periodic advertisement background task.

    The task reads interval from app_settings dynamically, so it will
    adapt to configuration changes without restart.
    """
    global _advert_task
    if _advert_task is None or _advert_task.done():
        _advert_task = asyncio.create_task(_periodic_advert_loop())
        logger.info("Started periodic advertisement task (interval configured in settings)")


async def stop_periodic_advert():
    """Stop the periodic advertisement background task."""
    global _advert_task
    if _advert_task and not _advert_task.done():
        _advert_task.cancel()
        try:
            await _advert_task
        except asyncio.CancelledError:
            pass
        _advert_task = None
        logger.info("Stopped periodic advertisement")


# Guard to prevent rebooting the radio more than once per session for clock skew
_clock_reboot_attempted: bool = False

# Skew tolerance in seconds — don't reboot if radio is only slightly off
_CLOCK_SKEW_REBOOT_THRESHOLD = 60

# Valid telemetry collection intervals (must divide 24 for predictable daily schedules)
_VALID_TELEMETRY_INTERVALS = [1, 2, 3, 4, 6, 8, 12, 24]

# Maximum autoevict retry attempts before giving up
_MAX_AUTOEVICT_RETRIES = 3


async def _attempt_clock_wraparound(mc) -> bool:
    """Set radio time to max uint32 to trigger a counter wrap-around.

    Returns True if the radio time dropped below system time after the wrap,
    False otherwise (including on any error).
    """
    try:
        result = await mc.commands.set_time(0xFFFFFFFF)
        if result.type != EventType.OK:
            return False
        await asyncio.sleep(0.5)
        get_result = await mc.commands.get_time()
        if get_result and get_result.type == EventType.CURRENT_TIME:
            radio_time = get_result.payload.get("time", 0)
            if radio_time < int(time.time()):
                return True
        return False
    except Exception as exc:
        logger.debug("Clock wraparound attempt failed: %s", exc)
        return False


async def sync_radio_time(mc, warn_on_failure: bool = True) -> bool:
    """Sync the radio's clock with the system time.

    Returns True if successful, False otherwise.
    """
    global _clock_reboot_attempted

    _log = logger.warning if warn_on_failure else logger.debug

    try:
        now = int(time.time())

        # SpiBackend implements set_time() directly on the backend instance.
        if isinstance(mc, RadioBackend):
            result = await mc.set_time(now)
            if result.type == EventType.OK:
                logger.debug("Synced radio time to %d (SPI)", now)
                return True
            return False

        # Experimental path: if the radio clock is ahead of system time, try
        # to force it to wrap through the uint32 boundary before the normal set.
        if settings.clowntown_do_clock_wraparound:
            try:
                get_result = await mc.commands.get_time()
                if get_result and get_result.type == EventType.CURRENT_TIME:
                    radio_time = get_result.payload.get("time", 0)
                    if radio_time > now:
                        await _attempt_clock_wraparound(mc)
            except Exception:
                pass

        result = await mc.commands.set_time(now)

        if result.type == EventType.OK:
            logger.debug("Synced radio time to %d", now)
            return True

        if result.payload and result.payload.get("reason") == "illegal_arg":
            # Firmware rejected the time update (e.g. hardware RTC with its own clock).
            # Determine whether the skew is large enough to warrant a reboot.
            should_reboot = True
            try:
                get_result = await mc.commands.get_time()
                if get_result and get_result.type == EventType.CURRENT_TIME:
                    radio_time = get_result.payload.get("time", 0)
                    skew = abs(radio_time - now)
                    if skew <= _CLOCK_SKEW_REBOOT_THRESHOLD:
                        should_reboot = False
            except Exception:
                pass  # Unknown skew → still try a reboot

            if should_reboot and not _clock_reboot_attempted:
                _clock_reboot_attempted = True
                await mc.commands.reboot()

            _log("Radio rejected time sync: skew exceeds threshold")
            return False

        return False

    except Exception as e:
        _log("Failed to sync radio time: %s", e, exc_info=True)
        return False


async def _periodic_sync_loop():
    """Background task that periodically syncs and offloads."""
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)
            cleanup_expired_acks()
            if not radio_manager.is_connected:
                continue

            try:
                async with radio_manager.radio_operation(
                    "periodic_sync",
                    blocking=False,
                ) as be:
                    if await should_run_full_periodic_sync(be):
                        await sync_and_offload_all(be)
                    await sync_radio_time(be, warn_on_failure=False)
            except RadioOperationBusyError:
                logger.debug("Skipping periodic sync: radio busy")
        except asyncio.CancelledError:
            logger.info("Periodic sync task cancelled")
            break
        except Exception as e:
            logger.error("Error in periodic sync: %s", e, exc_info=True)


def start_periodic_sync():
    """Start the periodic sync background task."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(_periodic_sync_loop())
        logger.info("Started periodic radio sync (interval: %ds)", SYNC_INTERVAL)


async def stop_periodic_sync():
    """Stop the periodic sync background task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
        logger.info("Stopped periodic radio sync")


# Throttling for contact sync to radio
_last_contact_sync: float = 0.0
CONTACT_SYNC_THROTTLE_SECONDS = 30  # Don't sync more than once per 30 seconds


async def get_contacts_selected_for_radio_sync() -> list[Contact]:
    """Return the contacts that would be loaded onto the radio right now."""
    app_settings = await AppSettingsRepository.get()
    max_contacts = _effective_radio_capacity(app_settings.max_radio_contacts)
    refill_target, _full_sync_trigger = _compute_radio_contact_limits(max_contacts)
    selected_contacts: list[Contact] = []
    selected_keys: set[str] = set()

    favorite_contacts_loaded = 0
    for contact in await ContactRepository.get_favorites():
        key = contact.public_key.lower()
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected_contacts.append(contact)
        favorite_contacts_loaded += 1
        if len(selected_contacts) >= max_contacts:
            break

    if len(selected_contacts) < refill_target:
        for contact in await ContactRepository.get_recently_dm_active_non_repeaters(
            limit=max_contacts
        ):
            key = contact.public_key.lower()
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected_contacts.append(contact)
            if len(selected_contacts) >= refill_target:
                break

    if len(selected_contacts) < refill_target:
        for contact in await ContactRepository.get_recently_advertised_non_repeaters(
            limit=max_contacts
        ):
            key = contact.public_key.lower()
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected_contacts.append(contact)
            if len(selected_contacts) >= refill_target:
                break

    logger.debug(
        "Selected %d contacts to sync (%d favorites, refill_target=%d, capacity=%d)",
        len(selected_contacts),
        favorite_contacts_loaded,
        refill_target,
        max_contacts,
    )
    return selected_contacts


async def _sync_contacts_to_radio_inner(be: RadioBackend) -> dict:
    """
    Core logic for loading contacts onto the radio.

    Fill order is:
    1. Favorite contacts
    2. Most recently interacted-with non-repeaters
    3. Most recently advert-heard non-repeaters without interaction history

    Favorite contacts are always reloaded first, up to the configured capacity.
    Additional non-favorite fill stops at the refill target (80% of capacity).

    Caller must hold the radio operation lock and pass a valid backend instance.
    """
    selected_contacts = await get_contacts_selected_for_radio_sync()
    return await _load_contacts_to_radio(be, selected_contacts)


async def ensure_contact_on_radio(
    public_key: str,
    *,
    force: bool = False,
    be: RadioBackend | None = None,
) -> dict:
    """Ensure one contact is loaded on the radio for ACK/routing support."""
    global _last_contact_sync

    now = time.time()
    if not force and (now - _last_contact_sync) < CONTACT_SYNC_THROTTLE_SECONDS:
        logger.debug(
            "Single-contact sync throttled (last sync %ds ago)",
            int(now - _last_contact_sync),
        )
        return {"loaded": 0, "throttled": True}

    try:
        contact = await ContactRepository.get_by_key_or_prefix(public_key)
    except AmbiguousPublicKeyPrefixError:
        logger.warning("Cannot sync favorite contact '%s': ambiguous key prefix", public_key)
        return {"loaded": 0, "error": "Ambiguous contact key prefix"}

    if not contact:
        logger.debug("Cannot sync favorite contact %s: not found", public_key[:12])
        return {"loaded": 0, "error": "Contact not found"}
    if len(contact.public_key) < 64:
        logger.debug("Cannot sync unresolved prefix-only contact %s to radio", public_key)
        return {"loaded": 0, "error": "Full contact key not yet known"}

    if be is not None:
        _last_contact_sync = now
        return await _load_contacts_to_radio(be, [contact])

    if not radio_manager.is_connected or radio_manager.backend is None:
        logger.debug("Cannot sync favorite contact to radio: not connected")
        return {"loaded": 0, "error": "Radio not connected"}

    try:
        async with radio_manager.radio_operation(
            "ensure_contact_on_radio",
            blocking=False,
        ) as be_inner:
            _last_contact_sync = now
            return await _load_contacts_to_radio(be_inner, [contact])
    except RadioOperationBusyError:
        logger.debug("Skipping favorite contact sync: radio busy")
        return {"loaded": 0, "busy": True}
    except Exception as e:
        logger.error("Error syncing favorite contact to radio: %s", e, exc_info=True)
        return {"loaded": 0, "error": str(e)}


async def _load_contacts_to_radio(mc_or_be, contacts: list[Contact]) -> dict:
    """Load the provided contacts onto the radio."""
    be = _to_backend(mc_or_be)
    loaded = 0
    already_on_radio = 0
    failed = 0

    for contact in contacts:
        if len(contact.public_key) < 64:
            logger.debug(
                "Skipping unresolved prefix-only contact %s during radio load", contact.public_key
            )
            continue
        radio_contact = be.get_contact_by_key_prefix(contact.public_key[:12])
        if radio_contact:
            already_on_radio += 1
            continue

        try:
            radio_contact_payload = contact.to_radio_dict()
            result = await be.add_contact(radio_contact_payload)
            if result.type == EventType.OK:
                loaded += 1
                logger.debug("Loaded contact %s to radio", contact.public_key[:12])
            else:
                failed += 1
                reason = result.payload
                hint = ""
                if reason is None:
                    hint = (
                        " (no response from radio — if this repeats, check for "
                        "serial port contention from another process or try a "
                        "power cycle)"
                    )
                logger.warning(
                    "Failed to load contact %s: %s%s",
                    contact.public_key[:12],
                    reason,
                    hint,
                )
        except Exception as e:
            failed += 1
            logger.warning(
                "Error loading contact %s with fields=%s radio_payload=%s: %s",
                contact.public_key[:12],
                _contact_sync_debug_fields(contact),
                locals().get("radio_contact_payload"),
                e,
                exc_info=True,
            )

    if loaded > 0 or failed > 0:
        logger.info(
            "Contact sync: loaded %d, already on radio %d, failed %d",
            loaded,
            already_on_radio,
            failed,
        )

    return {
        "loaded": loaded,
        "already_on_radio": already_on_radio,
        "failed": failed,
    }


async def sync_recent_contacts_to_radio(
    force: bool = False, be: RadioBackend | None = None, mc=None
) -> dict:
    """
    Load contacts to the radio for DM ACK support.

    Fill order is favorites, then recently contacted non-repeaters,
    then recently advert-heard non-repeaters. Favorites are always reloaded
    up to the configured capacity; additional non-favorite fill stops at the
    80% refill target.
    Only runs at most once every CONTACT_SYNC_THROTTLE_SECONDS unless forced.

    Args:
        force: Skip the throttle check.
        be: Optional RadioBackend instance. When provided, the caller already
            holds the radio operation lock and the inner logic runs directly.
            When None, this function acquires its own lock.

    Returns counts of contacts loaded.
    """
    global _last_contact_sync

    # Throttle unless forced
    now = time.time()
    if not force and (now - _last_contact_sync) < CONTACT_SYNC_THROTTLE_SECONDS:
        logger.debug("Contact sync throttled (last sync %ds ago)", int(now - _last_contact_sync))
        return {"loaded": 0, "throttled": True}

    # If caller provided a raw mc, wrap and run directly without acquiring the lock
    if mc is not None:
        _last_contact_sync = now
        return await _sync_contacts_to_radio_inner(_to_backend(mc))

    # If caller provided a backend instance, use it directly (caller holds the lock)
    if be is not None:
        _last_contact_sync = now
        return await _sync_contacts_to_radio_inner(be)

    if not radio_manager.is_connected or radio_manager.backend is None:
        logger.debug("Cannot sync contacts to radio: not connected")
        return {"loaded": 0, "error": "Radio not connected"}

    try:
        async with radio_manager.radio_operation(
            "sync_recent_contacts_to_radio",
            blocking=False,
        ) as be_inner:
            _last_contact_sync = now
            return await _sync_contacts_to_radio_inner(be_inner)
    except RadioOperationBusyError:
        logger.debug("Skipping contact sync to radio: radio busy")
        return {"loaded": 0, "busy": True}

    except Exception as e:
        logger.error("Error syncing contacts to radio: %s", e, exc_info=True)
        return {"loaded": 0, "error": str(e)}


async def _reconcile_radio_contacts_in_background(
    *,
    initial_radio_contacts: dict,
    expected_mc,
    autoevict: bool = False,
) -> None:
    """Yielding background task that reconciles radio contact state with the desired DB set.

    Non-autoevict mode:
      Phase 1 — delete contacts no longer wanted (re-checks desired set before each deletion).
      Phase 2 — add contacts that are wanted but missing from the radio.

    Autoevict mode (radio handles eviction on TABLE_FULL automatically):
      Skip deletions; add all desired contacts. Retries up to _MAX_AUTOEVICT_RETRIES times.
    """
    if autoevict:
        for _attempt in range(_MAX_AUTOEVICT_RETRIES + 1):
            desired = await get_contacts_selected_for_radio_sync()
            async with radio_manager.radio_operation(
                "background_contact_reconcile",
                blocking=True,
            ) as mc_from_lock:
                be = _to_backend(mc_from_lock)
                desired_in_lock = await get_contacts_selected_for_radio_sync()
                all_ok = True
                for contact in desired_in_lock:
                    if len(contact.public_key) < 64:
                        continue
                    if be.get_contact_by_key_prefix(contact.public_key[:12]):
                        continue
                    payload = contact.to_radio_dict()
                    payload["flags"] = 0  # strip favorite bit; let radio manage eviction
                    result = await be.add_contact(payload)
                    if (
                        result.type == EventType.ERROR
                        and (result.payload or {}).get("error_code") == 3
                    ):
                        broadcast_error(
                            "Radio contact table full during auto-evict fill",
                            "The radio's contact table is full and auto-evict did not free "
                            "space as expected. Try reducing MESHCORE_MAX_RADIO_CONTACTS or "
                            "set MESHCORE_LOAD_WITH_AUTOEVICT=true and restart.",
                        )
                        return
                    elif result.type != EventType.OK:
                        all_ok = False
                if all_ok:
                    return
            await asyncio.sleep(CONTACT_RECONCILE_BUSY_BACKOFF_SECONDS)
        return

    # Non-autoevict: phase 1 — remove unwanted contacts in batches
    current_on_radio: dict = dict(initial_radio_contacts)
    to_remove: list[tuple[str, dict]] = []

    for key, data in list(current_on_radio.items()):
        key_lower = key.lower()
        # Initial check: is this contact still in the desired set?
        initial_desired = await get_contacts_selected_for_radio_sync()
        initial_desired_keys = {c.public_key.lower() for c in initial_desired}
        if key_lower in initial_desired_keys:
            continue

        # Re-check before deleting (desired set may have changed)
        recheck_desired = await get_contacts_selected_for_radio_sync()
        recheck_desired_keys = {c.public_key.lower() for c in recheck_desired}
        if key_lower in recheck_desired_keys:
            continue

        to_remove.append((key, data))

        if len(to_remove) >= CONTACT_RECONCILE_BATCH_SIZE:
            async with radio_manager.radio_operation(
                "background_contact_reconcile",
                blocking=True,
            ) as mc_from_lock:
                be = _to_backend(mc_from_lock)
                for k, d in to_remove:
                    result = await be.remove_contact(d)
                    if result.type == EventType.OK:
                        current_on_radio.pop(k, None)
            to_remove = []
            await asyncio.sleep(CONTACT_RECONCILE_YIELD_SECONDS)

    # Flush remaining removals
    if to_remove:
        async with radio_manager.radio_operation(
            "background_contact_reconcile",
            blocking=True,
        ) as mc_from_lock:
            be = _to_backend(mc_from_lock)
            for k, d in to_remove:
                result = await be.remove_contact(d)
                if result.type == EventType.OK:
                    current_on_radio.pop(k, None)
        to_remove = []
        await asyncio.sleep(CONTACT_RECONCILE_YIELD_SECONDS)

    # Phase 2 — add desired contacts not currently on radio
    desired = await get_contacts_selected_for_radio_sync()
    current_keys = {k.lower() for k in current_on_radio}
    to_add: list[Contact] = []

    for contact in desired:
        if len(contact.public_key) < 64:
            continue
        if contact.public_key.lower() in current_keys:
            continue
        to_add.append(contact)

        if len(to_add) >= CONTACT_RECONCILE_BATCH_SIZE:
            async with radio_manager.radio_operation(
                "background_contact_reconcile",
                blocking=True,
            ) as mc_from_lock:
                be = _to_backend(mc_from_lock)
                for c in to_add:
                    if not be.get_contact_by_key_prefix(c.public_key[:12]):
                        await be.add_contact(c.to_radio_dict())
            to_add = []
            await asyncio.sleep(CONTACT_RECONCILE_YIELD_SECONDS)

    if to_add:
        async with radio_manager.radio_operation(
            "background_contact_reconcile",
            blocking=True,
        ) as mc_from_lock:
            be = _to_backend(mc_from_lock)
            for c in to_add:
                if not be.get_contact_by_key_prefix(c.public_key[:12]):
                    await be.add_contact(c.to_radio_dict())


def start_background_contact_reconciliation(
    *,
    initial_radio_contacts: dict,
    expected_mc,
    autoevict: bool = False,
) -> None:
    """Start the background contact reconcile task, cancelling any prior run."""
    global _contact_reconcile_task
    if _contact_reconcile_task and not _contact_reconcile_task.done():
        _contact_reconcile_task.cancel()
    _contact_reconcile_task = asyncio.create_task(
        _reconcile_radio_contacts_in_background(
            initial_radio_contacts=initial_radio_contacts,
            expected_mc=expected_mc,
            autoevict=autoevict,
        )
    )


async def stop_background_contact_reconciliation() -> None:
    """Stop the background contact reconcile task."""
    global _contact_reconcile_task
    if _contact_reconcile_task and not _contact_reconcile_task.done():
        _contact_reconcile_task.cancel()
        try:
            await _contact_reconcile_task
        except asyncio.CancelledError:
            pass
    _contact_reconcile_task = None


def _clamp_telemetry_interval(preferred: int, n_repeaters: int) -> int:
    """Clamp preferred interval up to the smallest valid interval that keeps polls ≤ 24/day."""
    min_legal = max(preferred, n_repeaters)
    for interval in _VALID_TELEMETRY_INTERVALS:
        if interval >= min_legal:
            return interval
    return 24


async def _collect_repeater_telemetry(mc, contact) -> bool:
    """Fetch status and optional LPP telemetry from one repeater and record it.

    Returns True on success, False on error.
    """
    try:
        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)
        if status is None:
            logger.debug("No status response from %s", contact.public_key[:12])
            return False

        data: dict = {
            "battery_volts": status.get("bat", 0) / 1000.0,
            "noise_floor_dbm": status.get("noise_floor", 0),
            "last_rssi_dbm": status.get("last_rssi", 0),
            "last_snr_db": status.get("last_snr", 0.0),
            "packets_received": status.get("nb_recv", 0),
            "packets_sent": status.get("nb_sent", 0),
            "airtime_seconds": status.get("airtime", 0),
            "rx_airtime_seconds": status.get("rx_airtime", 0),
            "uptime_seconds": status.get("uptime", 0),
            "tx_queue_len": status.get("tx_queue_len", 0),
            "sent_flood": status.get("sent_flood", 0),
            "sent_direct": status.get("sent_direct", 0),
        }

        # Best-effort LPP sensor collection
        try:
            lpp_raw = await mc.commands.req_telemetry_sync(
                contact.public_key, timeout=10, min_timeout=5
            )
            if lpp_raw:
                lpp_sensors = [
                    {"type_name": s.get("type"), "value": s.get("value")}
                    for s in lpp_raw
                    if not isinstance(s.get("value"), dict)
                ]
                if lpp_sensors:
                    data["lpp_sensors"] = lpp_sensors
        except Exception as exc:
            logger.debug(
                "LPP sensor fetch failed for %s (non-fatal): %s", contact.public_key[:12], exc
            )

        timestamp = int(time.time())
        await RepeaterTelemetryRepository.record(contact.public_key, timestamp, data)

        from app.fanout.manager import fanout_manager

        await fanout_manager.broadcast_telemetry(data)
        return True

    except Exception as exc:
        logger.warning(
            "Telemetry collection failed for %s: %s", contact.public_key[:12], exc, exc_info=True
        )
        return False


async def _run_telemetry_cycle(routed_only: bool = False) -> None:
    """Collect telemetry from all tracked repeaters (or only routed ones if routed_only)."""
    app_settings = await AppSettingsRepository.get()
    repeater_keys = app_settings.tracked_telemetry_repeaters

    for key in repeater_keys:
        contact = await ContactRepository.get_by_key(key)
        if contact is None:
            logger.debug("Tracked telemetry repeater %s not found in DB", key[:12])
            continue

        if routed_only and (
            contact.effective_route is None or contact.effective_route.path_len < 0
        ):
            logger.debug("Skipping flood-only repeater %s in routed-only telemetry cycle", key[:12])
            continue

        try:
            async with radio_manager.radio_operation(
                "telemetry_collect", blocking=False
            ) as mc_from_lock:
                await _collect_repeater_telemetry(mc_from_lock, contact)
        except RadioOperationBusyError:
            logger.debug("Skipping telemetry for %s: radio busy", key[:12])
        except Exception as exc:
            logger.warning("Telemetry collection error for %s: %s", key[:12], exc, exc_info=True)


async def _maybe_run_scheduled_cycle(now: datetime) -> None:
    """Evaluate the hour-modulo gate and dispatch a telemetry cycle if due."""
    app_settings = await AppSettingsRepository.get()
    n = len(app_settings.tracked_telemetry_repeaters)
    if n == 0:
        return

    interval = _clamp_telemetry_interval(app_settings.telemetry_interval_hours, n)
    hour = now.hour

    if hour % interval == 0:
        await _run_telemetry_cycle(routed_only=False)
    elif app_settings.telemetry_routed_hourly:
        await _run_telemetry_cycle(routed_only=True)


async def _telemetry_collect_loop() -> None:
    """Background scheduler that wakes at top of each UTC hour and runs telemetry if due."""
    # Initial boot guard: wait before first check so startup I/O settles
    await asyncio.sleep(60)

    # Post-boot boundary check: if the current hour already matches the schedule,
    # run immediately rather than waiting until the next top-of-hour.
    now = datetime.now(UTC)
    await _maybe_run_scheduled_cycle(now)

    while True:
        try:
            now = datetime.now(UTC)
            seconds_to_next_hour = 3600 - now.minute * 60 - now.second
            await asyncio.sleep(max(1, seconds_to_next_hour))

            now = datetime.now(UTC)
            await _maybe_run_scheduled_cycle(now)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Error in telemetry collect loop: %s", exc, exc_info=True)


def start_telemetry_collect() -> None:
    """Start the periodic telemetry collection background task."""
    global _telemetry_collect_task
    if _telemetry_collect_task is None or _telemetry_collect_task.done():
        _telemetry_collect_task = asyncio.create_task(_telemetry_collect_loop())
        logger.info("Started periodic telemetry collection task")


async def stop_telemetry_collect() -> None:
    """Stop the periodic telemetry collection background task."""
    global _telemetry_collect_task
    if _telemetry_collect_task and not _telemetry_collect_task.done():
        _telemetry_collect_task.cancel()
        try:
            await _telemetry_collect_task
        except asyncio.CancelledError:
            pass
    _telemetry_collect_task = None
