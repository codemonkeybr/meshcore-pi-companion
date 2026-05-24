"""Fanout module for Apprise push notifications."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.fanout.base import FanoutModule, get_fanout_message_text
from app.path_utils import split_path_hex

logger = logging.getLogger(__name__)

_MAX_SEND_ATTEMPTS = 3
_RETRY_DELAY_S = 2

DEFAULT_BODY_FORMAT_DM = "**DM:** {sender_name}: {text} **via:** [{hops_backticked}]"
DEFAULT_BODY_FORMAT_CHANNEL = (
    "**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]"
)
_DEFAULT_BODY_FORMAT_DM_NO_PATH = "**DM:** {sender_name}: {text}"
_DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH = "**{channel_name}:** {sender_name}: {text}"

# Plain-text variants (no markdown formatting)
DEFAULT_BODY_FORMAT_DM_PLAIN = "DM: {sender_name}: {text} via: [{hops}]"
DEFAULT_BODY_FORMAT_CHANNEL_PLAIN = "{channel_name}: {sender_name}: {text} via: [{hops}]"
_DEFAULT_BODY_FORMAT_DM_NO_PATH_PLAIN = "DM: {sender_name}: {text}"
_DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH_PLAIN = "{channel_name}: {sender_name}: {text}"

# Variables available for user format strings
FORMAT_VARIABLES = (
    "type",
    "text",
    "sender_name",
    "sender_key",
    "channel_name",
    "conversation_key",
    "hops",
    "hops_backticked",
    "hop_count",
    "rssi",
    "snr",
)


def _parse_urls(raw: str) -> list[str]:
    """Split multi-line URL string into individual URLs."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _normalize_discord_url(url: str) -> str:
    """Add avatar=no to Discord URLs to suppress identity override."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()

    is_discord = scheme in ("discord", "discords") or (
        scheme in ("http", "https")
        and host in ("discord.com", "discordapp.com")
        and parts.path.lower().startswith("/api/webhooks/")
    )
    if not is_discord:
        return url

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["avatar"] = "no"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _compute_hops(data: dict) -> tuple[str, str, int]:
    """Extract hop info from message data. Returns (hops, hops_backticked, hop_count)."""
    paths = data.get("paths")
    if paths and isinstance(paths, list) and len(paths) > 0:
        first_path = paths[0] if isinstance(paths[0], dict) else {}
        path_str = first_path.get("path", "")
        path_len = first_path.get("path_len")
    else:
        path_str = None
        path_len = None

    if path_str is None or path_str.strip() == "":
        return ("direct", "`direct`", 0)

    path_str = path_str.strip().lower()
    hop_count = path_len if isinstance(path_len, int) else len(path_str) // 2
    hops = split_path_hex(path_str, hop_count)
    if not hops:
        return ("direct", "`direct`", 0)

    return (
        ", ".join(hops),
        ", ".join(f"`{h}`" for h in hops),
        len(hops),
    )


def _build_template_vars(data: dict) -> dict[str, str]:
    """Build the variable dict for format string substitution."""
    hops_raw, hops_bt, hop_count = _compute_hops(data)

    paths = data.get("paths")
    rssi = ""
    snr = ""
    if paths and isinstance(paths, list) and len(paths) > 0:
        first_path = paths[0] if isinstance(paths[0], dict) else {}
        rssi_val = first_path.get("rssi")
        snr_val = first_path.get("snr")
        if rssi_val is not None:
            rssi = str(rssi_val)
        if snr_val is not None:
            snr = str(snr_val)

    return {
        "type": data.get("type", ""),
        "text": get_fanout_message_text(data),
        "sender_name": data.get("sender_name") or "Unknown",
        "sender_key": data.get("sender_key") or "",
        "channel_name": data.get("channel_name") or data.get("conversation_key", "channel"),
        "conversation_key": data.get("conversation_key", ""),
        "hops": hops_raw,
        "hops_backticked": hops_bt,
        "hop_count": str(hop_count),
        "rssi": rssi,
        "snr": snr,
    }


def _apply_format(fmt: str, variables: dict[str, str]) -> str:
    """Apply template variables in a single pass to avoid re-expanding substituted values."""
    import re

    def _replacer(m: re.Match[str]) -> str:
        key = m.group(1)
        return variables.get(key, m.group(0))

    return re.sub(r"\{(\w+)\}", _replacer, fmt)


def _format_body(
    data: dict,
    *,
    body_format_dm: str | None = None,
    body_format_channel: str | None = None,
    markdown: bool = True,
) -> str:
    """Build a notification body from message data using format strings."""
    if body_format_dm is None:
        body_format_dm = DEFAULT_BODY_FORMAT_DM if markdown else DEFAULT_BODY_FORMAT_DM_PLAIN
    if body_format_channel is None:
        body_format_channel = (
            DEFAULT_BODY_FORMAT_CHANNEL if markdown else DEFAULT_BODY_FORMAT_CHANNEL_PLAIN
        )
    variables = _build_template_vars(data)
    msg_type = data.get("type", "")
    fmt = body_format_dm if msg_type == "PRIV" else body_format_channel
    try:
        return _apply_format(fmt, variables)
    except Exception:
        logger.warning("Apprise format string error, falling back to default")
        if markdown:
            default = DEFAULT_BODY_FORMAT_DM if msg_type == "PRIV" else DEFAULT_BODY_FORMAT_CHANNEL
        else:
            default = (
                DEFAULT_BODY_FORMAT_DM_PLAIN
                if msg_type == "PRIV"
                else DEFAULT_BODY_FORMAT_CHANNEL_PLAIN
            )
        return _apply_format(default, variables)


def _send_sync(urls_raw: str, body: str, *, preserve_identity: bool, markdown: bool = True) -> bool:
    """Send notification synchronously via Apprise. Returns True on success."""
    import apprise as apprise_lib
    from apprise import NotifyFormat

    urls = _parse_urls(urls_raw)
    if not urls:
        return False

    notifier = apprise_lib.Apprise()
    for url in urls:
        if preserve_identity:
            url = _normalize_discord_url(url)
        notifier.add(url)

    body_fmt = NotifyFormat.MARKDOWN if markdown else NotifyFormat.TEXT
    return bool(notifier.notify(title="", body=body, body_format=body_fmt))


class AppriseModule(FanoutModule):
    """Sends push notifications via Apprise for incoming messages."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)

    async def on_message(self, data: dict) -> None:
        # Skip outgoing messages — only notify on incoming
        if data.get("outgoing"):
            return

        urls = self.config.get("urls", "")
        if not urls or not urls.strip():
            return

        preserve_identity = self.config.get("preserve_identity", True)
        markdown = self.config.get("markdown_format", True)

        # Read format strings; treat empty/whitespace as unset (use default).
        # Fall back to legacy include_path for pre-migration configs.
        body_format_dm = (self.config.get("body_format_dm") or "").strip() or None
        body_format_channel = (self.config.get("body_format_channel") or "").strip() or None
        if body_format_dm is None or body_format_channel is None:
            include_path = self.config.get("include_path", True)
            if body_format_dm is None:
                if markdown:
                    body_format_dm = (
                        DEFAULT_BODY_FORMAT_DM if include_path else _DEFAULT_BODY_FORMAT_DM_NO_PATH
                    )
                else:
                    body_format_dm = (
                        DEFAULT_BODY_FORMAT_DM_PLAIN
                        if include_path
                        else _DEFAULT_BODY_FORMAT_DM_NO_PATH_PLAIN
                    )
            if body_format_channel is None:
                if markdown:
                    body_format_channel = (
                        DEFAULT_BODY_FORMAT_CHANNEL
                        if include_path
                        else _DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH
                    )
                else:
                    body_format_channel = (
                        DEFAULT_BODY_FORMAT_CHANNEL_PLAIN
                        if include_path
                        else _DEFAULT_BODY_FORMAT_CHANNEL_NO_PATH_PLAIN
                    )

        body = _format_body(
            data,
            body_format_dm=body_format_dm,
            body_format_channel=body_format_channel,
            markdown=markdown,
        )

        last_exc: Exception | None = None
        for attempt in range(_MAX_SEND_ATTEMPTS):
            try:
                success = await asyncio.to_thread(
                    _send_sync,
                    urls,
                    body,
                    preserve_identity=preserve_identity,
                    markdown=markdown,
                )
                if success:
                    self._set_last_error(None)
                    return
                logger.warning(
                    "Apprise notification failed for module %s (attempt %d/%d)",
                    self.config_id,
                    attempt + 1,
                    _MAX_SEND_ATTEMPTS,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Apprise send error for module %s (attempt %d/%d): %s",
                    self.config_id,
                    attempt + 1,
                    _MAX_SEND_ATTEMPTS,
                    exc,
                )
            if attempt < _MAX_SEND_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_DELAY_S)

        # All attempts exhausted
        if last_exc is not None:
            self._set_last_error(str(last_exc))
        else:
            self._set_last_error("Apprise notify returned failure")

    @property
    def status(self) -> str:
        if not self.config.get("urls", "").strip():
            return "disconnected"
        if self.last_error:
            return "error"
        return "connected"
