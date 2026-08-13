"""
Bot execution module for automatic message responses.

This module provides functionality for executing user-defined Python code
in response to incoming messages. The user's code can process message data
and optionally return a response string or a list of strings.

SECURITY WARNING: This executes arbitrary Python code provided by the user.
It should only be enabled on trusted systems where the user understands
the security implications.
"""

import asyncio
import inspect
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Limit concurrent bot executions to prevent resource exhaustion
_bot_semaphore = asyncio.Semaphore(100)

# Dedicated thread pool for bot execution (separate from default executor)
_bot_executor = ThreadPoolExecutor(max_workers=100, thread_name_prefix="bot_")

# Timeout for bot code execution (seconds)
BOT_EXECUTION_TIMEOUT = 10

# Minimum spacing between bot message sends (seconds)
# This ensures repeaters have time to return to listening mode
BOT_MESSAGE_SPACING = 2.0

# Global state for rate limiting bot sends
_bot_send_lock = asyncio.Lock()
_last_bot_send_time: float = 0.0

# global container for persistent data storage between bot executions, will be added to execution namespace
_bot_globals: dict[str, Any] = {}


@dataclass(frozen=True)
class BotCallPlan:
    """How to call a validated bot() function."""

    call_style: str
    keyword_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class BotReply:
    """A structured bot response that may scope its outgoing message(s).

    Produced when a bot returns ``{"region": ..., "message": ...}``. The plain
    ``str``/``list[str]`` return shapes are unaffected and never produce a BotReply.

    ``flood_scope_override`` carries the per-send region decision for the reply:
      - ``None``  → no region specified; use the channel's persisted override
      - ``""``    → region explicitly cleared; send unscoped/plain flood
      - region    → scope the reply send to that region name
    """

    messages: list[str]
    flood_scope_override: str | None = None


def _coerce_bot_dict_reply(result: dict) -> "BotReply | None":
    """Normalize a ``{"region", "message"}`` bot return into a BotReply, or None.

    ``message`` may be a string or a list of strings (empties dropped). A missing
    ``message`` (or no valid messages) yields None. ``region`` is optional: absent
    means "use the channel default", ``None`` means "send unscoped", and a string
    is normalized to the radio's region form.
    """
    from app.region_scope import normalize_region_scope

    raw = result.get("message")
    if isinstance(raw, str):
        messages = [raw] if raw.strip() else []
    elif isinstance(raw, list):
        messages = [m for m in raw if isinstance(m, str) and m.strip()]
    else:
        logger.debug("Bot dict response 'message' must be a string or list of strings")
        return None
    if not messages:
        return None

    if "region" in result:
        region_value = result.get("region")
        if region_value is None:
            flood_scope_override: str | None = ""  # explicit: send unscoped
        elif isinstance(region_value, str):
            flood_scope_override = normalize_region_scope(region_value)
        else:
            logger.debug("Bot dict response 'region' must be a string or None")
            return None
    else:
        flood_scope_override = None  # no region specified: use channel default

    return BotReply(messages=messages, flood_scope_override=flood_scope_override)


def _analyze_bot_signature(bot_func_or_sig) -> BotCallPlan:
    """Validate bot() signature and return a supported call plan."""
    try:
        sig = (
            bot_func_or_sig
            if isinstance(bot_func_or_sig, inspect.Signature)
            else inspect.signature(bot_func_or_sig)
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Bot function signature could not be inspected") from exc

    params = sig.parameters
    param_values = tuple(params.values())
    positional_params = [
        p
        for p in param_values
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in param_values)
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in param_values)
    explicit_optional_names = tuple(
        name
        for name in ("is_outgoing", "path_bytes_per_hop", "packet_hash", "region", "scoped")
        if name in params
    )
    unsupported_required_kwonly = [
        p.name
        for p in param_values
        if p.kind == inspect.Parameter.KEYWORD_ONLY
        and p.default is inspect.Parameter.empty
        and p.name not in {"is_outgoing", "path_bytes_per_hop", "packet_hash", "region", "scoped"}
    ]
    if unsupported_required_kwonly:
        raise ValueError(
            "Bot function signature is not supported. Unsupported required keyword-only "
            "parameters: " + ", ".join(unsupported_required_kwonly)
        )

    positional_capacity = len(positional_params)
    base_args = [object()] * 8
    base_keyword_args: dict[str, object] = {
        "sender_name": object(),
        "sender_key": object(),
        "message_text": object(),
        "is_dm": object(),
        "channel_key": object(),
        "channel_name": object(),
        "sender_timestamp": object(),
        "path": object(),
    }
    candidate_specs: list[tuple[str, list[object], dict[str, object]]] = []
    keyword_args = dict(base_keyword_args)
    if has_kwargs or "is_outgoing" in params:
        keyword_args["is_outgoing"] = False
    if has_kwargs or "path_bytes_per_hop" in params:
        keyword_args["path_bytes_per_hop"] = 1
    if has_kwargs or "packet_hash" in params:
        keyword_args["packet_hash"] = ""
    if has_kwargs or "region" in params:
        keyword_args["region"] = None
    if has_kwargs or "scoped" in params:
        keyword_args["scoped"] = False
    candidate_specs.append(("keyword", [], keyword_args))

    if not has_kwargs and explicit_optional_names:
        kwargs: dict[str, object] = {}
        if has_kwargs or "is_outgoing" in params:
            kwargs["is_outgoing"] = False
        if has_kwargs or "path_bytes_per_hop" in params:
            kwargs["path_bytes_per_hop"] = 1
        if has_kwargs or "packet_hash" in params:
            kwargs["packet_hash"] = ""
        if has_kwargs or "region" in params:
            kwargs["region"] = None
        if has_kwargs or "scoped" in params:
            kwargs["scoped"] = False
        candidate_specs.append(("mixed_keyword", base_args, kwargs))

    if has_varargs or positional_capacity >= 11:
        candidate_specs.append(("positional_11", base_args + [False, 1, ""], {}))
    if has_varargs or positional_capacity >= 10:
        candidate_specs.append(("positional_10", base_args + [False, 1], {}))
    if has_varargs or positional_capacity >= 9:
        candidate_specs.append(("positional_9", base_args + [False], {}))
    if has_varargs or positional_capacity >= 8:
        candidate_specs.append(("legacy", base_args, {}))

    for call_style, args, kwargs in candidate_specs:
        try:
            sig.bind(*args, **kwargs)
        except TypeError:
            continue
        if call_style in {"keyword", "mixed_keyword"}:
            return BotCallPlan(call_style="keyword", keyword_args=tuple(kwargs.keys()))
        return BotCallPlan(call_style=call_style)

    raise ValueError(
        "Bot function signature is not supported. Use the default bot template as a reference. "
        "Supported trailing parameters are: path; path + is_outgoing; "
        "path + path_bytes_per_hop; path + is_outgoing + path_bytes_per_hop; "
        "path + is_outgoing + path_bytes_per_hop + packet_hash; "
        "or use **kwargs for forward compatibility (which also receives region and scoped)."
    )


def execute_bot_code(
    code: str,
    sender_name: str | None,
    sender_key: str | None,
    message_text: str,
    is_dm: bool,
    channel_key: str | None,
    channel_name: str | None,
    sender_timestamp: int | None,
    path: str | None,
    is_outgoing: bool = False,
    path_bytes_per_hop: int | None = None,
    packet_hash: str | None = None,
    region: str | None = None,
    scoped: bool = False,
) -> str | list[str] | BotReply | None:
    """
    Execute user-provided bot code with message context.

    The code should define a function:
    `bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing, path_bytes_per_hop, packet_hash)`
    or use named parameters / `**kwargs`.

    `region` and `scoped` are only delivered to bots that opt in via `**kwargs`
    or by naming the parameter; the positional call styles are unchanged for
    backward compatibility.

    The bot returns either None (no response), a string (single response message),
    a list of strings (multiple messages sent in order), or a dict
    `{"region": <name|None>, "message": <str|list[str]>}` to scope the reply to a
    region for this send only (`None`/empty region = unscoped flood). The dict
    form only affects channel replies; `region` is ignored for DM replies.

    Legacy bot functions with older signatures are detected via inspect and
    called without the newer parameters for backward compatibility.

    Args:
        code: Python code defining the bot function
        sender_name: Display name of the sender (may be None)
        sender_key: 64-char hex public key of sender for DMs, None for channel messages
        message_text: The message content
        is_dm: True for direct messages, False for channel messages
        channel_key: 32-char hex channel key for channel messages, None for DMs
        channel_name: Channel name (e.g. "#general" with hash), None for DMs
        sender_timestamp: Sender's timestamp from the message (may be None)
        path: Hex-encoded routing path (may be None)
        is_outgoing: True if this is our own outgoing message
        path_bytes_per_hop: Number of bytes per routing hop (1, 2, or 3), if known
        packet_hash: MeshCore packet hash (first 16 hex chars of SHA256, uppercase), if known
        scoped: True if the message carried a regional flood scope, False for
            plain/unscoped flood. Check this first — it is the meaningful signal.
            Set for scoped DMs too (flood-direct messages can carry a scope).
        region: Only meaningful when scoped is True. When scoped is False, region
            is always None and should be ignored. When scoped is True, region is
            the decoded region name, or None if the scope matched none of your
            known_regions (i.e. scoped, but region unrecognized). region is never
            enough on its own to tell "unscoped" from "unrecognized" — use scoped.

    Returns:
        Response string, list of strings, or None.

    Note: This executes arbitrary code. Only use with trusted input.
    """
    if not code or not code.strip():
        return None

    # Build execution namespace with allowed imports
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "_bot_globals": _bot_globals,
    }

    try:
        # Execute the user's code to define the bot function
        exec(code, namespace)
    except Exception:
        logger.exception("Bot code compilation failed")
        return None

    # Check if bot function was defined
    if "bot" not in namespace or not callable(namespace["bot"]):
        logger.debug("Bot code does not define a callable 'bot' function")
        return None

    bot_func = namespace["bot"]
    try:
        call_plan = _analyze_bot_signature(bot_func)
    except ValueError as exc:
        logger.error("%s", exc)
        return None

    try:
        # Call the bot function with appropriate signature
        if call_plan.call_style == "positional_11":
            result = bot_func(
                sender_name,
                sender_key,
                message_text,
                is_dm,
                channel_key,
                channel_name,
                sender_timestamp,
                path,
                is_outgoing,
                path_bytes_per_hop,
                packet_hash,
            )
        elif call_plan.call_style == "positional_10":
            result = bot_func(
                sender_name,
                sender_key,
                message_text,
                is_dm,
                channel_key,
                channel_name,
                sender_timestamp,
                path,
                is_outgoing,
                path_bytes_per_hop,
            )
        elif call_plan.call_style == "positional_9":
            result = bot_func(
                sender_name,
                sender_key,
                message_text,
                is_dm,
                channel_key,
                channel_name,
                sender_timestamp,
                path,
                is_outgoing,
            )
        elif call_plan.call_style == "keyword":
            keyword_args: dict[str, Any] = {}
            if "sender_name" in call_plan.keyword_args:
                keyword_args["sender_name"] = sender_name
            if "sender_key" in call_plan.keyword_args:
                keyword_args["sender_key"] = sender_key
            if "message_text" in call_plan.keyword_args:
                keyword_args["message_text"] = message_text
            if "is_dm" in call_plan.keyword_args:
                keyword_args["is_dm"] = is_dm
            if "channel_key" in call_plan.keyword_args:
                keyword_args["channel_key"] = channel_key
            if "channel_name" in call_plan.keyword_args:
                keyword_args["channel_name"] = channel_name
            if "sender_timestamp" in call_plan.keyword_args:
                keyword_args["sender_timestamp"] = sender_timestamp
            if "path" in call_plan.keyword_args:
                keyword_args["path"] = path
            if "is_outgoing" in call_plan.keyword_args:
                keyword_args["is_outgoing"] = is_outgoing
            if "path_bytes_per_hop" in call_plan.keyword_args:
                keyword_args["path_bytes_per_hop"] = path_bytes_per_hop
            if "packet_hash" in call_plan.keyword_args:
                keyword_args["packet_hash"] = packet_hash
            if "region" in call_plan.keyword_args:
                keyword_args["region"] = region
            if "scoped" in call_plan.keyword_args:
                keyword_args["scoped"] = scoped
            result = bot_func(**keyword_args)
        else:
            result = bot_func(
                sender_name,
                sender_key,
                message_text,
                is_dm,
                channel_key,
                channel_name,
                sender_timestamp,
                path,
            )

        # Validate result
        if result is None:
            return None
        if isinstance(result, dict):
            # Structured reply: {"region": ..., "message": str | list[str]}
            return _coerce_bot_dict_reply(result)
        if isinstance(result, str):
            return result if result.strip() else None
        if isinstance(result, list):
            # Filter to non-empty strings only
            valid_messages = [msg for msg in result if isinstance(msg, str) and msg.strip()]
            return valid_messages if valid_messages else None

        logger.debug("Bot function returned unsupported type: %s", type(result))
        return None

    except Exception:
        logger.exception("Bot function execution failed")
        return None


async def process_bot_response(
    response: str | list[str] | BotReply,
    is_dm: bool,
    sender_key: str,
    channel_key: str | None,
) -> None:
    """
    Send the bot's response message(s) using the existing message sending endpoints.

    For DMs, sends a direct message back to the sender.
    For channel messages, sends to the same channel.

    Bot messages are rate-limited to ensure at least BOT_MESSAGE_SPACING seconds
    between sends, giving repeaters time to return to listening mode.

    Args:
        response: The response to send — a string, a list of messages to send in
            order, or a BotReply carrying a per-send region scope
        is_dm: Whether the original message was a DM
        sender_key: Public key of the original sender (for DM replies)
        channel_key: Channel key for channel message replies
    """
    # Normalize to (messages, flood_scope_override) for uniform processing.
    if isinstance(response, BotReply):
        messages = response.messages
        flood_scope_override = response.flood_scope_override
    elif isinstance(response, str):
        messages = [response]
        flood_scope_override = None
    else:
        messages = response
        flood_scope_override = None

    for message_text in messages:
        await _send_single_bot_message(
            message_text, is_dm, sender_key, channel_key, flood_scope_override
        )


async def _send_single_bot_message(
    message_text: str,
    is_dm: bool,
    sender_key: str,
    channel_key: str | None,
    flood_scope_override: str | None = None,
) -> None:
    """
    Send a single bot message with rate limiting.

    Args:
        message_text: The message text to send
        is_dm: Whether the original message was a DM
        sender_key: Public key of the original sender (for DM replies)
        channel_key: Channel key for channel message replies
        flood_scope_override: Per-send region scope for channel replies (None =
            channel default, "" = unscoped, region name = scope to that region).
            Ignored for DMs, which are not region-scoped.
    """
    global _last_bot_send_time

    from app.models import SendChannelMessageRequest, SendDirectMessageRequest
    from app.routers.messages import send_channel_message, send_direct_message

    # Serialize bot sends and enforce minimum spacing
    async with _bot_send_lock:
        # Calculate how long since last bot send
        now = time.monotonic()
        time_since_last = now - _last_bot_send_time

        if _last_bot_send_time > 0 and time_since_last < BOT_MESSAGE_SPACING:
            wait_time = BOT_MESSAGE_SPACING - time_since_last
            logger.debug("Rate limiting bot send, waiting %.2fs", wait_time)
            await asyncio.sleep(wait_time)

        try:
            if is_dm:
                if flood_scope_override is not None:
                    logger.debug(
                        "Ignoring bot region scope %r on DM reply (DMs are not region-scoped)",
                        flood_scope_override,
                    )
                logger.info("Bot sending DM reply to %s", sender_key[:12])
                request = SendDirectMessageRequest(destination=sender_key, text=message_text)
                await send_direct_message(request)
            elif channel_key:
                logger.info("Bot sending channel reply to %s", channel_key[:8])
                request = SendChannelMessageRequest(
                    channel_key=channel_key,
                    text=message_text,
                    flood_scope_override=flood_scope_override,
                )
                await send_channel_message(request)
            else:
                logger.warning("Cannot send bot response: no destination")
                return  # Don't update timestamp if we didn't send
        except HTTPException as e:
            logger.error("Bot failed to send response: %s", e.detail, exc_info=True)
            return  # Don't update timestamp on failure
        except Exception:
            logger.exception("Bot failed to send response")
            return  # Don't update timestamp on failure

        # Update last send time after successful send
        _last_bot_send_time = time.monotonic()
