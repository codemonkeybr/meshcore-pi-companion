"""MQTT publisher for forwarding mesh network events to an MQTT broker."""

from __future__ import annotations

import logging
import ssl
from typing import Any, Protocol

from app.fanout.mqtt_base import BaseMqttPublisher

logger = logging.getLogger(__name__)


class PrivateMqttSettings(Protocol):
    """Attributes expected on the settings object for the private MQTT publisher."""

    mqtt_broker_host: str
    mqtt_broker_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_use_tls: bool
    mqtt_tls_insecure: bool
    mqtt_publish_messages: bool
    mqtt_publish_raw_packets: bool


class MqttPublisher(BaseMqttPublisher):
    """Manages an MQTT connection and publishes mesh network events."""

    _backoff_max = 3600
    _log_prefix = "MQTT"

    def _is_configured(self) -> bool:
        """Check if MQTT is configured and has something to publish."""
        s: PrivateMqttSettings | None = self._settings
        return bool(
            s and s.mqtt_broker_host and (s.mqtt_publish_messages or s.mqtt_publish_raw_packets)
        )

    def _build_client_kwargs(self, settings: object) -> dict[str, Any]:
        s: PrivateMqttSettings = settings  # type: ignore[assignment]
        return {
            "hostname": s.mqtt_broker_host,
            "port": s.mqtt_broker_port,
            "username": s.mqtt_username or None,
            "password": s.mqtt_password or None,
            "tls_context": self._build_tls_context(s),
        }

    def _on_connected(self, settings: object) -> tuple[str, str]:
        s: PrivateMqttSettings = settings  # type: ignore[assignment]
        return ("MQTT connected", f"{s.mqtt_broker_host}:{s.mqtt_broker_port}")

    def _on_error(self) -> tuple[str, str]:
        return ("MQTT connection failure", "Please correct the settings or disable.")

    @staticmethod
    def _build_tls_context(settings: PrivateMqttSettings) -> ssl.SSLContext | None:
        """Build TLS context from settings, or None if TLS is disabled."""
        if not settings.mqtt_use_tls:
            return None
        ctx = ssl.create_default_context()
        if settings.mqtt_tls_insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _build_message_topic(prefix: str, data: dict[str, Any]) -> str:
    """Build MQTT topic for a decrypted message."""
    msg_type = data.get("type", "")
    conversation_key = data.get("conversation_key", "unknown")

    if msg_type == "PRIV":
        return f"{prefix}/dm:{conversation_key}"
    elif msg_type == "CHAN":
        return f"{prefix}/gm:{conversation_key}"
    return f"{prefix}/message:{conversation_key}"


def _build_raw_packet_topic(prefix: str, data: dict[str, Any]) -> str:
    """Build MQTT topic for a raw packet."""
    info = data.get("decrypted_info")
    if info and isinstance(info, dict):
        contact_key = info.get("contact_key")
        channel_key = info.get("channel_key")
        if contact_key:
            return f"{prefix}/raw/dm:{contact_key}"
        if channel_key:
            return f"{prefix}/raw/gm:{channel_key}"
    return f"{prefix}/raw/unrouted"
