"""
Bot execution module for automatic message responses.

This module provides functionality for executing user-defined Python code
in response to incoming messages. The user's code can process message data
and optionally return a response string.

SECURITY WARNING: This executes arbitrary Python code provided by the user.
It should only be enabled on trusted systems where the user understands
the security implications.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Limit concurrent bot executions to prevent resource exhaustion
_bot_semaphore = asyncio.Semaphore(3)

# Dedicated thread pool for bot execution (separate from default executor)
_bot_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bot_")

# Timeout for bot code execution (seconds)
BOT_EXECUTION_TIMEOUT = 10

# Minimum spacing between bot message sends (seconds)
# This ensures repeaters have time to return to listening mode
BOT_MESSAGE_SPACING = 2.0

# Global state for rate limiting bot sends
_bot_send_lock = asyncio.Lock()
_last_bot_send_time: float = 0.0


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
) -> str | None:
    """
    Execute user-provided bot code with message context.

    The code should define a function:
    `bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path)`
    that returns either None (no response) or a string (response message).

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

    Returns:
        Response string if the bot returns one, None otherwise.

    Note: This executes arbitrary code. Only use with trusted input.
    """
    if not code or not code.strip():
        return None

    # Build execution namespace with allowed imports
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
    }

    try:
        # Execute the user's code to define the bot function
        exec(code, namespace)
    except Exception as e:
        logger.warning("Bot code compilation failed: %s", e)
        return None

    # Check if bot function was defined
    if "bot" not in namespace or not callable(namespace["bot"]):
        logger.debug("Bot code does not define a callable 'bot' function")
        return None

    bot_func = namespace["bot"]

    try:
        # Call the bot function with message context
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
        if isinstance(result, str):
            return result if result.strip() else None

        logger.debug("Bot function returned non-string: %s", type(result))
        return None

    except Exception as e:
        logger.warning("Bot function execution failed: %s", e)
        return None


async def process_bot_response(
    response: str,
    is_dm: bool,
    sender_key: str,
    channel_key: str | None,
) -> None:
    """
    Send the bot's response message using the existing message sending endpoints.

    For DMs, sends a direct message back to the sender.
    For channel messages, sends to the same channel.

    Bot messages are rate-limited to ensure at least BOT_MESSAGE_SPACING seconds
    between sends, giving repeaters time to return to listening mode.

    Args:
        response: The response text to send
        is_dm: Whether the original message was a DM
        sender_key: Public key of the original sender (for DM replies)
        channel_key: Channel key for channel message replies
    """
    global _last_bot_send_time

    from app.models import SendChannelMessageRequest, SendDirectMessageRequest
    from app.routers.messages import send_channel_message, send_direct_message
    from app.websocket import broadcast_event

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
                logger.info("Bot sending DM reply to %s", sender_key[:12])
                request = SendDirectMessageRequest(destination=sender_key, text=response)
                message = await send_direct_message(request)
                # Broadcast to WebSocket (endpoint returns to HTTP caller, bot needs explicit broadcast)
                broadcast_event("message", message.model_dump())
            elif channel_key:
                logger.info("Bot sending channel reply to %s", channel_key[:8])
                request = SendChannelMessageRequest(channel_key=channel_key, text=response)
                message = await send_channel_message(request)
                # Broadcast to WebSocket
                broadcast_event("message", message.model_dump())
            else:
                logger.warning("Cannot send bot response: no destination")
                return  # Don't update timestamp if we didn't send
        except HTTPException as e:
            logger.error("Bot failed to send response: %s", e.detail)
            return  # Don't update timestamp on failure
        except Exception as e:
            logger.error("Bot failed to send response: %s", e)
            return  # Don't update timestamp on failure

        # Update last send time after successful send
        _last_bot_send_time = time.monotonic()


async def run_bot_for_message(
    sender_name: str | None,
    sender_key: str | None,
    message_text: str,
    is_dm: bool,
    channel_key: str | None,
    channel_name: str | None = None,
    sender_timestamp: int | None = None,
    path: str | None = None,
    is_outgoing: bool = False,
) -> None:
    """
    Run the bot for an incoming message if enabled.

    This is the main entry point called by message handlers after
    a message is successfully decrypted and stored.

    Args:
        sender_name: Display name of the sender
        sender_key: 64-char hex public key of sender (DMs only, None for channels)
        message_text: The message content
        is_dm: True for direct messages, False for channel messages
        channel_key: Channel key for channel messages
        channel_name: Channel name (e.g. "#general"), None for DMs
        sender_timestamp: Sender's timestamp from the message
        path: Hex-encoded routing path
        is_outgoing: Whether this is our own outgoing message (skip bot)
    """
    # Don't respond to our own outgoing messages
    if is_outgoing:
        return

    # Early check if bot is enabled (will re-check after sleep)
    from app.repository import AppSettingsRepository

    settings = await AppSettingsRepository.get()
    if not settings.bot_enabled or not settings.bot_code:
        return

    # Try to acquire semaphore (limit concurrent bot executions)
    if not _bot_semaphore.locked():
        pass  # Semaphore available
    else:
        logger.debug("Bot execution queue full, skipping message")
        return

    async with _bot_semaphore:
        logger.debug(
            "Running bot for message from %s (is_dm=%s)",
            sender_name or (sender_key[:12] if sender_key else "unknown"),
            is_dm,
        )

        # Wait for the initiating message's retransmissions to propagate through the mesh
        await asyncio.sleep(2)

        # Re-check settings after sleep (user may have disabled bot)
        settings = await AppSettingsRepository.get()
        if not settings.bot_enabled or not settings.bot_code:
            logger.debug("Bot disabled during wait, skipping")
            return

        # Execute bot code in a dedicated thread pool with timeout
        loop = asyncio.get_event_loop()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    _bot_executor,
                    execute_bot_code,
                    settings.bot_code,
                    sender_name,
                    sender_key,
                    message_text,
                    is_dm,
                    channel_key,
                    channel_name,
                    sender_timestamp,
                    path,
                ),
                timeout=BOT_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Bot execution timed out after %ds", BOT_EXECUTION_TIMEOUT)
            return
        except Exception as e:
            logger.warning("Bot execution error: %s", e)
            return

        # Send response if any
        if response:
            await process_bot_response(response, is_dm, sender_key or "", channel_key)
