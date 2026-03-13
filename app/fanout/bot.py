"""Fanout module wrapping bot execution logic."""

from __future__ import annotations

import asyncio
import logging

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)


def _derive_path_bytes_per_hop(paths: object, path_value: str | None) -> int | None:
    """Derive hop width from the first serialized message path when possible."""
    if not isinstance(path_value, str) or not path_value:
        return None
    if not isinstance(paths, list) or not paths:
        return None

    first_path = paths[0]
    if not isinstance(first_path, dict):
        return None

    path_hops = first_path.get("path_len")
    if not isinstance(path_hops, int) or path_hops <= 0:
        return None

    path_hex_chars = len(path_value)
    if path_hex_chars % 2 != 0:
        return None

    path_bytes = path_hex_chars // 2
    if path_bytes % path_hops != 0:
        return None

    hop_width = path_bytes // path_hops
    if hop_width not in (1, 2, 3):
        return None

    return hop_width


class BotModule(FanoutModule):
    """Wraps a single bot's code execution and response routing.

    Each BotModule represents one bot configuration. It receives decoded
    messages via ``on_message``, executes the bot's Python code in a
    background task (after a 2-second settle delay), and sends any response
    back through the radio.
    """

    def __init__(self, config_id: str, config: dict, *, name: str = "Bot") -> None:
        super().__init__(config_id, config, name=name)
        self._tasks: set[asyncio.Task] = set()
        self._active = True

    async def stop(self) -> None:
        self._active = False
        for task in self._tasks:
            task.cancel()
        # Wait briefly for tasks to acknowledge cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def on_message(self, data: dict) -> None:
        """Kick off bot execution in a background task so we don't block dispatch."""
        task = asyncio.create_task(self._run_for_message(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_for_message(self, data: dict) -> None:
        from app.fanout.bot_exec import (
            BOT_EXECUTION_TIMEOUT,
            execute_bot_code,
            process_bot_response,
        )

        code = self.config.get("code", "")
        if not code or not code.strip():
            return

        msg_type = data.get("type", "")
        is_dm = msg_type == "PRIV"
        conversation_key = data.get("conversation_key", "")
        logger.debug(
            "Bot '%s' starting for type=%s conversation=%s outgoing=%s",
            self.name,
            msg_type or "unknown",
            conversation_key[:12] if conversation_key else "(none)",
            bool(data.get("outgoing", False)),
        )

        # Extract bot parameters from broadcast data
        if is_dm:
            sender_key = data.get("sender_key") or conversation_key
            is_outgoing = data.get("outgoing", False)
            message_text = data.get("text", "")
            channel_key = None
            channel_name = None

            # Outgoing DMs: sender is us, not the contact
            if is_outgoing:
                sender_name = None
            else:
                sender_name = data.get("sender_name")
                if sender_name is None:
                    from app.repository import ContactRepository

                    contact = await ContactRepository.get_by_key(conversation_key)
                    sender_name = contact.name if contact else None
        else:
            sender_key = None
            is_outgoing = bool(data.get("outgoing", False))
            sender_name = data.get("sender_name")
            channel_key = conversation_key

            channel_name = data.get("channel_name")
            if channel_name is None:
                from app.repository import ChannelRepository

                channel = await ChannelRepository.get_by_key(conversation_key)
                channel_name = channel.name if channel else None

            # Strip "sender: " prefix from channel message text
            text = data.get("text", "")
            if sender_name and text.startswith(f"{sender_name}: "):
                message_text = text[len(f"{sender_name}: ") :]
            else:
                message_text = text

        sender_timestamp = data.get("sender_timestamp")
        path_value = data.get("path")
        paths = data.get("paths")
        # Message model serializes paths as list of dicts; extract first path string
        if path_value is None and paths and isinstance(paths, list) and len(paths) > 0:
            path_value = paths[0].get("path") if isinstance(paths[0], dict) else None
        path_bytes_per_hop = _derive_path_bytes_per_hop(paths, path_value)

        # Wait for message to settle (allows retransmissions to be deduped)
        await asyncio.sleep(2)

        # Execute bot code in thread pool with timeout
        from app.fanout.bot_exec import _bot_executor, _bot_semaphore

        async with _bot_semaphore:
            loop = asyncio.get_running_loop()
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        _bot_executor,
                        execute_bot_code,
                        code,
                        sender_name,
                        sender_key,
                        message_text,
                        is_dm,
                        channel_key,
                        channel_name,
                        sender_timestamp,
                        path_value,
                        is_outgoing,
                        path_bytes_per_hop,
                    ),
                    timeout=BOT_EXECUTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("Bot '%s' execution timed out", self.name)
                return
            except Exception:
                logger.exception("Bot '%s' execution error", self.name)
                return

        if response and self._active:
            await process_bot_response(response, is_dm, sender_key or "", channel_key)

    @property
    def status(self) -> str:
        return "connected"
