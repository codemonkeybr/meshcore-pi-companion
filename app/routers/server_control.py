import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from meshcore import EventType

from app.models import (
    CONTACT_TYPE_REPEATER,
    CONTACT_TYPE_ROOM,
    CommandResponse,
    Contact,
    RepeaterLoginResponse,
)
from app.radio_sync import (
    _store_pending_channel_message,
    _store_pending_direct_message,
    drain_pending_messages,
)
from app.routers.contacts import _ensure_on_radio
from app.services.radio_runtime import radio_runtime as radio_manager

if TYPE_CHECKING:
    from meshcore.events import Event

logger = logging.getLogger(__name__)

SERVER_LOGIN_RESPONSE_TIMEOUT_SECONDS = 5.0


def _meshcore_from_backend(be) -> Any:
    """Return the underlying MeshCore; ClientBackend has _mc, SpiBackend is passed through."""
    from app.radio_backend import RadioBackend

    if isinstance(be, RadioBackend):
        return getattr(be, "_mc", be)
    return be


def _monotonic() -> float:
    """Wrapper around time.monotonic() for testability."""
    return time.monotonic()


def get_server_contact_label(contact: Contact) -> str:
    """Return a user-facing label for server-capable contacts."""
    if contact.type == CONTACT_TYPE_REPEATER:
        return "repeater"
    if contact.type == CONTACT_TYPE_ROOM:
        return "room server"
    return "server"


def require_server_capable_contact(
    contact: Contact,
    *,
    allowed_types: tuple[int, ...] = (CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM),
) -> None:
    """Raise 400 if the contact does not support server control/login features."""
    if contact.type not in allowed_types:
        expected = ", ".join(str(value) for value in allowed_types)
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a supported server contact (type={contact.type}, expected one of {expected})",
        )


def _login_rejected_message(label: str) -> str:
    return (
        f"The {label} replied but did not confirm this login. "
        f"Existing access may still allow some {label} operations, but privileged actions may fail."
    )


def _login_send_failed_message(label: str) -> str:
    return (
        f"The login request could not be sent to the {label}. "
        f"You're free to attempt interaction; try logging in again if authenticated actions fail."
    )


def _login_timeout_message(label: str) -> str:
    return (
        f"No login confirmation was heard from the {label}. "
        "That can mean the password was wrong or the reply was missed in transit. "
        "You're free to attempt interaction; try logging in again if authenticated actions fail."
    )


def _login_flood_retry_timeout_message(label: str) -> str:
    return (
        f"No login confirmation was heard from the {label}, including a retry sent as flood "
        "in case the stored route was stale. That can mean the password was wrong, the "
        f"{label} is out of range, or the reply was missed in transit. "
        "You're free to attempt interaction; try logging in again if authenticated actions fail."
    )


def extract_response_text(event) -> str:
    """Extract text from a CLI response event, stripping the firmware '> ' prefix."""
    text = event.payload.get("text", str(event.payload))
    if text.startswith("> "):
        text = text[2:]
    return text


async def _flush_pending_messages(mc) -> None:
    """Drain the radio's pending-message buffer before issuing a CLI command.

    A CLI response that arrived after a previous command already returned can
    sit buffered in the radio. Without this flush, the next command's fetch
    could pull that stale response and mis-attribute it as the new command's
    answer (the firmware does not correlate responses to requests). Draining
    first routes any real DMs/channel messages to storage and lets stale CLI
    responses (txt_type=1) be dropped by ``event_handlers.on_contact_message``,
    so they cannot be returned as this command's answer.

    This shrinks — but cannot fully eliminate — same-contact straddle
    mis-attribution: a reply that is still in flight when we send can only be
    bounded by a protocol-level request id, which the wire format lacks.
    """
    try:
        drained = await drain_pending_messages(mc)
        if drained:
            logger.debug("Flushed %d buffered message(s) before CLI send", drained)
    except Exception:
        logger.debug("Pre-send message flush failed", exc_info=True)


async def fetch_contact_cli_response(
    mc,
    target_pubkey_prefix: str,
    timeout: float = 20.0,
) -> "Event | None":
    """Fetch a CLI response (txt_type=1) from a specific contact.

    CLI responses arrive as ``CONTACT_MSG_RECV`` events, and the dispatcher
    clones every such event to *all* subscribers. The permanent handler in
    ``event_handlers.on_contact_message`` can therefore consume (and drop) a
    response in the gap between this loop's ``get_msg`` polls, producing a
    spurious timeout even though the response was delivered.

    To close that race we hold a request-scoped subscription for the target's
    CLI responses for the whole window. Whichever path observes the response
    first wins — ``get_msg``'s return value on the happy path, or the
    subscription when ``get_msg`` misses it — and the subscription is torn down
    in ``finally`` so nothing outlives this call (no global state, so a late or
    duplicate response cannot leak into an unrelated later fetch).

    ``get_msg`` is still polled to pump the radio into delivering buffered
    frames and to route any unrelated DMs/channel messages to storage.
    """
    loop = asyncio.get_running_loop()
    response_future: asyncio.Future = loop.create_future()

    def _capture(event: "Event") -> None:
        # Dispatcher invokes sync callbacks inline with a cloned event; the
        # attribute filter guarantees this only fires for the target's CLI
        # responses, so we resolve with the first one seen.
        if not response_future.done():
            response_future.set_result(event)

    subscription = mc.subscribe(
        EventType.CONTACT_MSG_RECV,
        _capture,
        attribute_filters={"pubkey_prefix": target_pubkey_prefix, "txt_type": 1},
    )

    try:
        deadline = _monotonic() + timeout

        while _monotonic() < deadline:
            if response_future.done():
                return response_future.result()

            try:
                result = await mc.commands.get_msg(timeout=2.0)
            except TimeoutError:
                continue
            except Exception as exc:
                logger.debug("get_msg() exception: %s", exc)
                await asyncio.sleep(1.0)
                continue

            if result.type == EventType.NO_MORE_MSGS:
                # The subscription may have captured a late delivery the radio
                # didn't hand back through this poll; prefer it over sleeping.
                if response_future.done():
                    return response_future.result()
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
                logger.debug(
                    "Storing non-target DM (from=%s, txt_type=%d) consumed while waiting for %s",
                    msg_prefix,
                    txt_type,
                    target_pubkey_prefix,
                )
                await _store_pending_direct_message(result)
                continue

            if result.type == EventType.CHANNEL_MSG_RECV:
                logger.debug(
                    "Storing channel message (channel_idx=%s) consumed during CLI fetch",
                    result.payload.get("channel_idx"),
                )
                await _store_pending_channel_message(mc, result.payload)
                continue

            logger.debug("Unexpected event type %s during CLI fetch, skipping", result.type)

        # Final grace check in case a delivery raced the deadline.
        if response_future.done():
            return response_future.result()
        logger.warning(
            "No CLI response from contact %s within %.1fs", target_pubkey_prefix, timeout
        )
        return None
    finally:
        subscription.unsubscribe()


async def _attempt_server_login(
    mc,
    contact: Contact,
    password: str,
    *,
    contact_label: str,
    response_timeout: float,
) -> RepeaterLoginResponse:
    """Send one login and wait for the reply.

    Subscriptions are per-attempt because the resolving future can only be
    settled once — a retry needs a fresh pair.
    """
    pubkey_prefix = contact.public_key[:12].lower()
    loop = asyncio.get_running_loop()
    login_future = loop.create_future()

    def _resolve_login(event_type: EventType, message: str | None = None) -> None:
        if login_future.done():
            return
        login_future.set_result(
            RepeaterLoginResponse(
                status="ok" if event_type == EventType.LOGIN_SUCCESS else "error",
                authenticated=event_type == EventType.LOGIN_SUCCESS,
                message=message,
            )
        )

    success_subscription = mc.subscribe(
        EventType.LOGIN_SUCCESS,
        lambda _event: _resolve_login(EventType.LOGIN_SUCCESS),
        attribute_filters={"pubkey_prefix": pubkey_prefix},
    )
    failed_subscription = mc.subscribe(
        EventType.LOGIN_FAILED,
        lambda _event: _resolve_login(
            EventType.LOGIN_FAILED,
            _login_rejected_message(contact_label),
        ),
        attribute_filters={"pubkey_prefix": pubkey_prefix},
    )

    try:
        logger.info("Sending login to %s %s", contact_label, contact.public_key[:12])
        login_result = await mc.commands.send_login(contact.public_key, password)

        if login_result.type == EventType.ERROR:
            return RepeaterLoginResponse(
                status="error",
                authenticated=False,
                message=f"{_login_send_failed_message(contact_label)} ({login_result.payload})",
            )

        try:
            return await asyncio.wait_for(login_future, timeout=response_timeout)
        except TimeoutError:
            logger.warning(
                "No login response from %s %s within %.1fs",
                contact_label,
                contact.public_key[:12],
                response_timeout,
            )
            return RepeaterLoginResponse(
                status="timeout",
                authenticated=False,
                message=_login_timeout_message(contact_label),
            )
    finally:
        success_subscription.unsubscribe()
        failed_subscription.unsubscribe()


async def prepare_authenticated_contact_connection(
    mc,
    contact: Contact,
    password: str,
    *,
    label: str | None = None,
    response_timeout: float = SERVER_LOGIN_RESPONSE_TIMEOUT_SECONDS,
) -> RepeaterLoginResponse:
    """Prepare connection to a server-capable contact by adding it to the radio and logging in.

    A login that draws no reply at all may mean the stored direct route has gone
    stale, so it escalates to one flood retry — mirroring the DM send path, which
    already resets the path before its final attempt. This is deliberately more
    than the reference clients do: firmware ``sendLogin`` and ``send_login_sync``
    are both single-shot, and firmware only clears a stale path when the *host*
    asks it to (``CMD_RESET_PATH``). Escalating is still the right call because
    the server side treats an inbound flood as its cue to relearn the return path
    (``simple_repeater``/``simple_room_server``: ``if (is_flood)
    client->out_path_len = OUT_PATH_UNKNOWN``), so a flood login is exactly what
    repairs a broken route in both directions.

    Escalation is bounded to a single extra attempt and only fires when:
      - the first attempt timed out. An explicit ``LOGIN_FAILED`` means the
        server heard us and refused, so the route is fine and retrying would
        just hammer it with bad credentials. A send error is a local radio
        problem that a different route will not fix.
      - the first attempt actually used a route. If the contact was already on
        flood, the retry would be byte-identical for no gain.

    ``reset_path`` clears the route on the radio only; the stored contact route
    is left alone, so the next ``add_contact`` re-stages it. That matches the DM
    retry and keeps a single bad login from discarding a route that may be fine.
    """
    contact_label = label or get_server_contact_label(contact)

    try:
        logger.info("Adding %s %s to radio", contact_label, contact.public_key[:12])
        await _ensure_on_radio(mc, contact)

        response = await _attempt_server_login(
            mc,
            contact,
            password,
            contact_label=contact_label,
            response_timeout=response_timeout,
        )
        if response.status != "timeout":
            return response

        if contact.effective_route_source == "flood":
            logger.debug(
                "Login to %s %s timed out on flood; no route to escalate from",
                contact_label,
                contact.public_key[:12],
            )
            return response

        logger.info(
            "Login to %s %s timed out on the %s route; resetting path and retrying as flood",
            contact_label,
            contact.public_key[:12],
            contact.effective_route_source,
        )
        reset_result = await mc.commands.reset_path(contact.public_key)
        if reset_result is None:
            logger.warning(
                "No response from radio for reset_path to %s before flood login retry",
                contact.public_key[:12],
            )
            return response
        if reset_result.type == EventType.ERROR:
            logger.warning(
                "Failed to reset path before flood login retry to %s: %s",
                contact.public_key[:12],
                reset_result.payload,
            )
            return response

        # Deliberately no _ensure_on_radio here — re-adding the contact would
        # restore the very route we just cleared, and the retry would go direct.
        flood_response = await _attempt_server_login(
            mc,
            contact,
            password,
            contact_label=contact_label,
            response_timeout=response_timeout,
        )
        if flood_response.status == "timeout":
            return RepeaterLoginResponse(
                status="timeout",
                authenticated=False,
                message=_login_flood_retry_timeout_message(contact_label),
            )
        return flood_response
    except HTTPException as exc:
        logger.warning(
            "%s login setup failed for %s: %s",
            contact_label.capitalize(),
            contact.public_key[:12],
            exc.detail,
        )
        return RepeaterLoginResponse(
            status="error",
            authenticated=False,
            message=f"{_login_send_failed_message(contact_label)} ({exc.detail})",
        )


async def batch_cli_fetch(
    contact: Contact,
    operation_name: str,
    commands: list[tuple[str, str]],
) -> dict[str, str | None]:
    """Send a batch of CLI commands to a server-capable contact and collect responses.

    Each command acquires and releases the radio lock independently so that
    other operations (sends, syncs) can slip in between commands.
    """
    results: dict[str, str | None] = {field: None for _, field in commands}

    for index, (cmd, field) in enumerate(commands):
        if index > 0:
            # Yield briefly so queued operations can acquire the lock.
            await asyncio.sleep(0.25)

        async with radio_manager.radio_operation(
            operation_name,
            pause_polling=True,
            suspend_auto_fetch=True,
        ) as be:
            mc = _meshcore_from_backend(be)
            # Re-ensure contact is loaded each iteration; another operation
            # may have evicted it while we didn't hold the lock.
            await _ensure_on_radio(mc, contact)
            await asyncio.sleep(1.0)  # settle after add_contact

            # Clear any stale buffered CLI response from a prior command so it
            # cannot be pulled and mis-attributed to this one.
            await _flush_pending_messages(mc)

            send_result = await mc.commands.send_cmd(contact.public_key, cmd)
            if send_result.type == EventType.ERROR:
                logger.debug("Command '%s' send error: %s", cmd, send_result.payload)
                continue

            response_event = await fetch_contact_cli_response(
                mc, contact.public_key[:12], timeout=10.0
            )
            if response_event is not None:
                results[field] = extract_response_text(response_event)
            else:
                logger.warning("No response for command '%s' (%s)", cmd, field)

    return results


class _RepeaterBinaryReqType(Enum):
    """Binary request types not (yet) wrapped by the installed meshcore library.

    ``REQ_TYPE_GET_OWNER_INFO`` (0x07) was added at repeater ``FIRMWARE_VER_LEVEL >= 2``.
    The firmware serves it from ``handleRequest`` with no admin gate, so any
    logged-in client — including a guest — can fetch it, unlike the CLI
    ``get owner.info`` / ``ver`` path which the firmware only routes for admins.
    """

    OWNER_INFO = 0x07


def _parse_owner_info_payload(data_hex: str) -> dict[str, str | None] | None:
    """Parse a REQ_TYPE_GET_OWNER_INFO (0x07) binary response payload.

    The repeater replies with ``"{firmware}\\n{name}\\n{owner_info}"`` (the 4-byte
    request tag is already stripped by the library's frame parser, so
    ``payload["data"]`` starts at the firmware string). ``owner_info`` may itself
    contain newlines, so only the first two separators are split.
    """
    if not data_hex:
        return None
    try:
        raw = bytes.fromhex(data_hex)
    except ValueError:
        return None
    text = raw.decode("utf-8", "ignore").strip("\x00")
    if not text.strip():
        return None
    parts = text.split("\n", 2)
    firmware = parts[0].strip() if len(parts) > 0 else ""
    name = parts[1].strip() if len(parts) > 1 else ""
    owner = parts[2].strip() if len(parts) > 2 else ""
    return {
        "firmware_version": firmware or None,
        "name": name or None,
        "owner_info": owner or None,
    }


async def fetch_repeater_owner_info_binary(
    contact: Contact,
    *,
    operation_name: str = "repeater_owner_info_binary",
    timeout: float = 10.0,
    min_timeout: float = 5.0,
) -> dict[str, str | None] | None:
    """Fetch firmware/name/owner via the guest-accessible binary request (0x07).

    This is the path Liam and other apps use to show owner info + firmware for a
    guest: a binary ``REQ_TYPE_GET_OWNER_INFO`` request rather than an admin-only
    CLI command. Returns ``None`` when the repeater does not answer — older
    firmware (level < 2), not logged in, or out of range — so callers can fall
    back or leave the fields blank. See issue #306.
    """
    async with radio_manager.radio_operation(
        operation_name, pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for reply routing.
        await _ensure_on_radio(mc, contact)
        await asyncio.sleep(1.0)  # settle after add_contact

        send_result = await mc.commands.send_binary_req(
            contact.public_key,
            _RepeaterBinaryReqType.OWNER_INFO,
            timeout=timeout,
            min_timeout=min_timeout,
        )
        if send_result.type == EventType.ERROR:
            logger.debug("owner-info binary req send error: %s", send_result.payload)
            return None

        expected_ack = send_result.payload.get("expected_ack")
        if expected_ack is None:
            logger.debug("owner-info binary req missing expected_ack: %s", send_result.payload)
            return None
        exp_tag = expected_ack.hex()

        wait_timeout = (
            timeout if timeout > 0 else send_result.payload.get("suggested_timeout", 4000) / 800
        )
        wait_timeout = max(wait_timeout, min_timeout)

        response = await mc.wait_for_event(
            EventType.BINARY_RESPONSE,
            attribute_filters={"tag": exp_tag},
            timeout=wait_timeout,
        )
        if response is None:
            logger.info(
                "No owner-info binary response from %s within %.1fs",
                contact.public_key[:12],
                wait_timeout,
            )
            return None

    return _parse_owner_info_payload(response.payload.get("data", ""))


async def send_contact_cli_command(
    contact: Contact,
    command: str,
    *,
    operation_name: str,
) -> CommandResponse:
    """Send a CLI command to a server-capable contact and return the text response."""
    label = get_server_contact_label(contact)

    async with radio_manager.radio_operation(
        operation_name,
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as be:
        mc = _meshcore_from_backend(be)
        logger.info("Adding %s %s to radio", label, contact.public_key[:12])
        await _ensure_on_radio(mc, contact)
        await asyncio.sleep(1.0)

        # Clear any stale buffered CLI response from a prior command so it
        # cannot be pulled and mis-attributed to this one.
        await _flush_pending_messages(mc)

        logger.info("Sending command to %s %s: %s", label, contact.public_key[:12], command)
        send_result = await mc.commands.send_cmd(contact.public_key, command)

        if send_result.type == EventType.ERROR:
            raise HTTPException(
                status_code=422, detail=f"Failed to send command: {send_result.payload}"
            )

        response_event = await fetch_contact_cli_response(mc, contact.public_key[:12])

        if response_event is None:
            logger.warning(
                "No response from %s %s for command: %s",
                label,
                contact.public_key[:12],
                command,
            )
            return CommandResponse(
                command=command,
                response="(no response - command may have been processed)",
            )

        response_text = extract_response_text(response_event)
        sender_timestamp = response_event.payload.get(
            "sender_timestamp",
            response_event.payload.get("timestamp"),
        )
        logger.info(
            "Received response from %s %s: %s",
            label,
            contact.public_key[:12],
            response_text,
        )

        return CommandResponse(
            command=command,
            response=response_text,
            sender_timestamp=sender_timestamp,
        )
