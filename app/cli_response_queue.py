"""Queue for repeater CLI responses when using SPI backend.

On SPI, pymc_core's send_repeater_command returns before the repeater's reply
arrives; the reply is delivered later via the packet pipeline. When we skip
storing repeater CLI responses in create_dm_message_from_decrypted, we put
the response text here so _batch_cli_fetch can await it instead of timing out.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# pubkey_prefix (12-char) -> queue of response strings (one per send_cmd in batch)
_waiters: dict[str, asyncio.Queue[str]] = {}


def register(prefix: str) -> asyncio.Queue[str]:
    """Register a waiter for CLI responses from the given repeater (12-char prefix).
    Returns a queue that will receive one response string per command."""
    q: asyncio.Queue[str] = asyncio.Queue()
    _waiters[prefix] = q
    logger.debug("CLI response queue registered for %s", prefix)
    return q


def put(prefix: str, text: str) -> None:
    """Deliver a CLI response to the waiter for this repeater, if any."""
    q = _waiters.get(prefix)
    if q is not None:
        try:
            q.put_nowait(text)
        except asyncio.QueueFull:
            logger.warning("CLI response queue full for %s, dropping: %s", prefix, text[:50])
    else:
        logger.debug("No CLI waiter for %s, dropping response: %s", prefix, text[:50])


def unregister(prefix: str) -> None:
    """Remove the waiter for this prefix. Call when batch is done or timed out."""
    if prefix in _waiters:
        del _waiters[prefix]
        logger.debug("CLI response queue unregistered for %s", prefix)
