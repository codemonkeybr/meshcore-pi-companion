"""Fanout module wrapping the private MQTT publisher."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from app.fanout.base import FanoutModule
from app.fanout.mqtt import MqttPublisher, _build_message_topic, _build_raw_packet_topic

logger = logging.getLogger(__name__)


def _config_to_settings(config: dict) -> SimpleNamespace:
    """Map a fanout config blob to a settings namespace for the MqttPublisher."""
    return SimpleNamespace(
        mqtt_broker_host=config.get("broker_host", ""),
        mqtt_broker_port=config.get("broker_port", 1883),
        mqtt_username=config.get("username", ""),
        mqtt_password=config.get("password", ""),
        mqtt_use_tls=config.get("use_tls", False),
        mqtt_tls_insecure=config.get("tls_insecure", False),
        mqtt_topic_prefix=config.get("topic_prefix", "meshcore"),
        mqtt_publish_messages=True,
        mqtt_publish_raw_packets=True,
    )


class MqttPrivateModule(FanoutModule):
    """Wraps an MqttPublisher instance for private MQTT forwarding."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._publisher = MqttPublisher()
        self._publisher.set_integration_name(name or config_id)

    async def start(self) -> None:
        settings = _config_to_settings(self.config)
        await self._publisher.start(settings)

    async def stop(self) -> None:
        await self._publisher.stop()

    async def on_message(self, data: dict) -> None:
        if not self._publisher.connected or self._publisher._settings is None:
            return
        prefix = self.config.get("topic_prefix", "meshcore")
        topic = _build_message_topic(prefix, data)
        await self._publisher.publish(topic, data)

    async def on_raw(self, data: dict) -> None:
        if not self._publisher.connected or self._publisher._settings is None:
            return
        prefix = self.config.get("topic_prefix", "meshcore")
        topic = _build_raw_packet_topic(prefix, data)
        await self._publisher.publish(topic, data)

    @property
    def status(self) -> str:
        if not self.config.get("broker_host"):
            return "disconnected"
        if self._publisher.last_error:
            return "error"
        return "connected" if self._publisher.connected else "disconnected"

    @property
    def last_error(self) -> str | None:
        return self._publisher.last_error
