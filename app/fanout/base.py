"""Base class for fanout integration modules."""

from __future__ import annotations


def _broadcast_fanout_health() -> None:
    """Push updated fanout status to connected frontend clients."""
    from app.services.radio_runtime import radio_runtime as radio_manager
    from app.websocket import broadcast_health

    broadcast_health(radio_manager.is_connected, radio_manager.connection_info)


class FanoutModule:
    """Base class for all fanout integrations.

    Each module wraps a specific integration (MQTT, webhook, etc.) and
    receives dispatched messages/packets from the FanoutManager.

    Subclasses must override the ``status`` property.
    """

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        self.config_id = config_id
        self.config = config
        self.name = name
        self._last_error: str | None = None

    async def start(self) -> None:
        """Start the module (e.g. connect to broker). Override for persistent connections."""

    async def stop(self) -> None:
        """Stop the module (e.g. disconnect from broker)."""

    async def on_message(self, data: dict) -> None:
        """Called for decoded messages (DM/channel). Override if needed."""

    async def on_raw(self, data: dict) -> None:
        """Called for raw RF packets. Override if needed."""

    async def on_contact(self, data: dict) -> None:
        """Called for contact upserts (adverts, sync). Override if needed."""

    async def on_telemetry(self, data: dict) -> None:
        """Called for repeater telemetry snapshots. Override if needed."""

    async def on_health(self, data: dict) -> None:
        """Called for periodic radio health snapshots. Override if needed."""

    @property
    def status(self) -> str:
        """Return 'connected', 'disconnected', or 'error'."""
        raise NotImplementedError

    @property
    def last_error(self) -> str | None:
        """Return the most recent retained operator-facing error, if any."""
        return self._last_error

    def _set_last_error(self, value: str | None) -> None:
        """Update the retained error and broadcast health when it changes."""
        if self._last_error == value:
            return
        self._last_error = value
        _broadcast_fanout_health()


def get_fanout_message_text(data: dict) -> str:
    """Return the best human-readable message body for fanout consumers.

    Channel messages are stored with the rendered sender label embedded in the
    text (for example ``"Alice: hello"``). Human-facing integrations that also
    carry ``sender_name`` should strip that duplicated prefix when it matches
    the payload sender exactly.
    """

    text = data.get("text", "")
    if not isinstance(text, str):
        return ""

    if data.get("type") != "CHAN":
        return text

    sender_name = data.get("sender_name")
    if not isinstance(sender_name, str) or not sender_name:
        return text

    prefix, separator, remainder = text.partition(": ")
    if separator and prefix == sender_name:
        return remainder

    return text
