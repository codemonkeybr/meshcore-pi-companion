"""Fanout module wrapping the community MQTT publisher."""

from __future__ import annotations

import logging
import re
import string
from types import SimpleNamespace
from typing import Any

from app.fanout.base import FanoutModule
from app.fanout.community_mqtt import CommunityMqttPublisher, _format_raw_packet

logger = logging.getLogger(__name__)

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DEFAULT_PACKET_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/packets"
_TOPIC_TEMPLATE_FIELD_CANONICAL = {
    "iata": "IATA",
    "public_key": "PUBLIC_KEY",
}


def _normalize_topic_template(topic_template: str) -> str:
    """Normalize packet topic template fields to canonical uppercase placeholders."""
    template = topic_template.strip() or _DEFAULT_PACKET_TOPIC_TEMPLATE
    parts: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for literal_text, field_name, format_spec, conversion in parsed:
            parts.append(literal_text)
            if field_name is None:
                continue
            normalized_field = _TOPIC_TEMPLATE_FIELD_CANONICAL.get(field_name.lower())
            if normalized_field is None:
                raise ValueError(f"Unsupported topic template field(s): {field_name}")
            replacement = ["{", normalized_field]
            if conversion:
                replacement.extend(["!", conversion])
            if format_spec:
                replacement.extend([":", format_spec])
            replacement.append("}")
            parts.append("".join(replacement))
    except ValueError:
        raise

    return "".join(parts)


def _config_to_settings(config: dict) -> SimpleNamespace:
    """Map a fanout config blob to a settings namespace for the CommunityMqttPublisher."""
    return SimpleNamespace(
        community_mqtt_enabled=True,
        community_mqtt_broker_host=config.get("broker_host", "mqtt-us-v1.letsmesh.net"),
        community_mqtt_broker_port=config.get("broker_port", 443),
        community_mqtt_transport=config.get("transport", "websockets"),
        community_mqtt_use_tls=config.get("use_tls", True),
        community_mqtt_tls_verify=config.get("tls_verify", True),
        community_mqtt_auth_mode=config.get("auth_mode", "token"),
        community_mqtt_username=config.get("username", ""),
        community_mqtt_password=config.get("password", ""),
        community_mqtt_iata=config.get("iata", ""),
        community_mqtt_email=config.get("email", ""),
        community_mqtt_token_audience=config.get("token_audience", ""),
    )


def _render_packet_topic(topic_template: str, *, iata: str, public_key: str) -> str:
    """Render the configured raw-packet publish topic."""
    template = _normalize_topic_template(topic_template)
    return template.format(IATA=iata, PUBLIC_KEY=public_key)


class MqttCommunityModule(FanoutModule):
    """Wraps a CommunityMqttPublisher for community packet sharing."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._publisher = CommunityMqttPublisher()

    async def start(self) -> None:
        settings = _config_to_settings(self.config)
        await self._publisher.start(settings)

    async def stop(self) -> None:
        await self._publisher.stop()

    async def on_message(self, data: dict) -> None:
        # Community MQTT only publishes raw packets, not decoded messages.
        pass

    async def on_raw(self, data: dict) -> None:
        if not self._publisher.connected or self._publisher._settings is None:
            return
        await _publish_community_packet(self._publisher, self.config, data)

    @property
    def status(self) -> str:
        if self._publisher._is_configured():
            return "connected" if self._publisher.connected else "disconnected"
        return "disconnected"


async def _publish_community_packet(
    publisher: CommunityMqttPublisher,
    config: dict,
    data: dict[str, Any],
) -> None:
    """Format and publish a raw packet to the community broker."""
    try:
        from app.keystore import get_public_key
        from app.services.radio_runtime import radio_runtime as radio_manager

        public_key = get_public_key()
        if public_key is None:
            return

        pubkey_hex = public_key.hex().upper()

        device_name = ""
        if radio_manager.backend and radio_manager.backend.self_info:
            device_name = radio_manager.backend.self_info.get("name", "")

        packet = _format_raw_packet(data, device_name, pubkey_hex)
        iata = config.get("iata", "").upper().strip()
        if not _IATA_RE.fullmatch(iata):
            logger.debug("Community MQTT: skipping publish — no valid IATA code configured")
            return
        topic = _render_packet_topic(
            str(config.get("topic_template", _DEFAULT_PACKET_TOPIC_TEMPLATE)),
            iata=iata,
            public_key=pubkey_hex,
        )

        await publisher.publish(topic, packet)

    except Exception as e:
        logger.warning("Community MQTT broadcast error: %s", e, exc_info=True)
