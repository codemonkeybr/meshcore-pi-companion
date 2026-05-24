"""Home Assistant MQTT Discovery fanout module.

Publishes HA-compatible discovery configs and state updates so that mesh
network devices appear natively in Home Assistant via its built-in MQTT
integration. No custom HA component is needed.

Entity types created:
- Local radio: binary_sensor (connectivity) + sensors (noise floor, battery,
  uptime, RSSI, SNR, airtime, packet counts)
- Per tracked repeater: sensor entities for telemetry fields
- Per tracked contact: device_tracker for GPS position
- Messages: event entity for scope-matched messages
"""

from __future__ import annotations

import logging
import ssl
from types import SimpleNamespace
from typing import Any

from app.fanout.base import FanoutModule, get_fanout_message_text
from app.fanout.mqtt_base import BaseMqttPublisher

logger = logging.getLogger(__name__)

# ── Repeater telemetry sensor definitions ─────────────────────────────────

_REPEATER_SENSORS: list[dict[str, Any]] = [
    {
        "field": "battery_volts",
        "name": "Battery Voltage",
        "object_id": "battery_voltage",
        "device_class": "voltage",
        "state_class": "measurement",
        "unit": "V",
        "precision": 2,
    },
    {
        "field": "noise_floor_dbm",
        "name": "Noise Floor",
        "object_id": "noise_floor",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "unit": "dBm",
        "precision": 0,
    },
    {
        "field": "last_rssi_dbm",
        "name": "Last RSSI",
        "object_id": "last_rssi",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "unit": "dBm",
        "precision": 0,
    },
    {
        "field": "last_snr_db",
        "name": "Last SNR",
        "object_id": "last_snr",
        "device_class": None,
        "state_class": "measurement",
        "unit": "dB",
        "precision": 1,
    },
    {
        "field": "packets_received",
        "name": "Packets Received",
        "object_id": "packets_received",
        "device_class": None,
        "state_class": "total_increasing",
        "unit": None,
        "precision": 0,
    },
    {
        "field": "packets_sent",
        "name": "Packets Sent",
        "object_id": "packets_sent",
        "device_class": None,
        "state_class": "total_increasing",
        "unit": None,
        "precision": 0,
    },
    {
        "field": "recv_errors",
        "name": "RX Errors",
        "object_id": "recv_errors",
        "device_class": None,
        "state_class": "total_increasing",
        "unit": None,
        "precision": 0,
    },
    {
        "field": "uptime_seconds",
        "name": "Uptime",
        "object_id": "uptime",
        "device_class": "duration",
        "state_class": None,
        "unit": "s",
        "precision": 0,
    },
]

# ── LPP sensor metadata ─────────────────────────────────────────────────

_LPP_HA_META: dict[str, dict[str, Any]] = {
    "temperature": {"device_class": "temperature", "unit": "°C", "precision": 1},
    "humidity": {"device_class": "humidity", "unit": "%", "precision": 1},
    "barometer": {"device_class": "atmospheric_pressure", "unit": "hPa", "precision": 1},
    "voltage": {"device_class": "voltage", "unit": "V", "precision": 2},
    "current": {"device_class": "current", "unit": "A", "precision": 3},
    "luminosity": {"device_class": "illuminance", "unit": "lux", "precision": 0},
    "power": {"device_class": "power", "unit": "W", "precision": 1},
    "energy": {"device_class": "energy", "unit": "kWh", "precision": 2},
    "distance": {"device_class": "distance", "unit": "m", "precision": 3},
    "concentration": {"device_class": None, "unit": "ppm", "precision": 0},
    "direction": {"device_class": None, "unit": "°", "precision": 0},
    "altitude": {"device_class": None, "unit": "m", "precision": 1},
}


def _lpp_sensor_key(type_name: str, channel: int) -> str:
    """Build the flat telemetry-payload key for an LPP sensor."""
    return f"lpp_{type_name}_ch{channel}"


def _assign_lpp_keys(lpp_sensors: list[dict]) -> list[tuple[dict, str, int]]:
    """Pair each LPP sensor dict with a disambiguated flat key and occurrence.

    First occurrence keeps the base key (``lpp_temperature_ch1``), occurrence=1;
    subsequent duplicates of the same (type_name, channel) get ``_2``, ``_3``, etc.
    """
    counts: dict[str, int] = {}
    result: list[tuple[dict, str, int]] = []
    for sensor in lpp_sensors:
        base = _lpp_sensor_key(sensor.get("type_name", "unknown"), sensor.get("channel", 0))
        n = counts.get(base, 0) + 1
        counts[base] = n
        result.append((sensor, base if n == 1 else f"{base}_{n}", n))
    return result


def _repeater_telemetry_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the flat HA state payload for a repeater telemetry snapshot."""
    payload: dict[str, Any] = {}
    for sensor in _REPEATER_SENSORS:
        field = sensor["field"]
        if field is not None:
            payload[field] = data.get(field)

    for sensor, key, _ in _assign_lpp_keys(data.get("lpp_sensors", []) or []):
        payload[key] = sensor.get("value")

    return payload


def _contact_telemetry_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the flat HA state payload for a contact LPP telemetry snapshot.

    Unlike repeaters, contacts only have LPP sensor data — no battery_volts,
    noise_floor_dbm, packets_received, etc.
    """
    payload: dict[str, Any] = {}
    for sensor, key, _ in _assign_lpp_keys(data.get("lpp_sensors", []) or []):
        payload[key] = sensor.get("value")
    return payload


def _lpp_discovery_configs(
    prefix: str,
    pub_key: str,
    device: dict,
    lpp_sensors: list[dict],
    state_topic: str,
) -> list[tuple[str, dict]]:
    """Build HA discovery configs for a repeater's LPP sensors."""
    configs: list[tuple[str, dict]] = []
    for sensor, field, occurrence in _assign_lpp_keys(lpp_sensors):
        type_name = sensor.get("type_name", "unknown")
        channel = sensor.get("channel", 0)
        meta = _LPP_HA_META.get(type_name, {})

        nid = _node_id(pub_key)
        object_id = field
        display = type_name.replace("_", " ").title()
        name = (
            f"{display} (Ch {channel})"
            if occurrence == 1
            else f"{display} (Ch {channel}) #{occurrence}"
        )

        cfg: dict[str, Any] = {
            "name": name,
            "unique_id": f"meshcore_{nid}_{object_id}",
            "device": device,
            "state_topic": state_topic,
            "value_template": "{{ value_json." + field + " }}",
            "state_class": "measurement",
            "expire_after": 36000,
        }
        if meta.get("device_class"):
            cfg["device_class"] = meta["device_class"]
        if meta.get("unit"):
            cfg["unit_of_measurement"] = meta["unit"]
        if meta.get("precision") is not None:
            cfg["suggested_display_precision"] = meta["precision"]

        topic = f"homeassistant/sensor/meshcore_{nid}/{object_id}/config"
        configs.append((topic, cfg))

    return configs


# ── Local radio sensor definitions ────────────────────────────────────────

_RADIO_SENSORS: list[dict[str, Any]] = [
    {
        "field": "noise_floor_dbm",
        "name": "Noise Floor",
        "object_id": "noise_floor",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "unit": "dBm",
        "precision": 0,
    },
    {
        "field": "battery_volts",
        "name": "Battery",
        "object_id": "battery",
        "device_class": "voltage",
        "state_class": "measurement",
        "unit": "V",
        "precision": 2,
    },
    {
        "field": "uptime_secs",
        "name": "Uptime",
        "object_id": "uptime",
        "device_class": "duration",
        "state_class": None,
        "unit": "s",
        "precision": 0,
    },
    {
        "field": "last_rssi",
        "name": "Last RSSI",
        "object_id": "last_rssi",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "unit": "dBm",
        "precision": 0,
    },
    {
        "field": "last_snr",
        "name": "Last SNR",
        "object_id": "last_snr",
        "device_class": None,
        "state_class": "measurement",
        "unit": "dB",
        "precision": 1,
    },
    {
        "field": "tx_air_secs",
        "name": "TX Airtime",
        "object_id": "tx_airtime",
        "device_class": "duration",
        "state_class": "total_increasing",
        "unit": "s",
        "precision": 0,
    },
    {
        "field": "rx_air_secs",
        "name": "RX Airtime",
        "object_id": "rx_airtime",
        "device_class": "duration",
        "state_class": "total_increasing",
        "unit": "s",
        "precision": 0,
    },
    {
        "field": "packets_recv",
        "name": "Packets Received",
        "object_id": "packets_received",
        "device_class": None,
        "state_class": "total_increasing",
        "unit": None,
        "precision": 0,
    },
    {
        "field": "packets_sent",
        "name": "Packets Sent",
        "object_id": "packets_sent",
        "device_class": None,
        "state_class": "total_increasing",
        "unit": None,
        "precision": 0,
    },
]


def _node_id(public_key: str) -> str:
    """Derive a stable, MQTT-safe node identifier from a public key."""
    return public_key[:12].lower()


def _device_payload(
    public_key: str,
    name: str,
    model: str,
    *,
    via_device_key: str | None = None,
) -> dict[str, Any]:
    """Build an HA device registry fragment."""
    dev: dict[str, Any] = {
        "identifiers": [f"meshcore_{_node_id(public_key)}"],
        "name": name or public_key[:12],
        "manufacturer": "MeshCore",
        "model": model,
    }
    if via_device_key:
        dev["via_device"] = f"meshcore_{_node_id(via_device_key)}"
    return dev


# ── MQTT publisher subclass ───────────────────────────────────────────────


class _HaMqttPublisher(BaseMqttPublisher):
    """Thin MQTT lifecycle wrapper for the HA discovery module."""

    _backoff_max = 3600
    _log_prefix = "HA-MQTT"

    def __init__(self) -> None:
        super().__init__()
        self._on_connected_callback: Any = None

    def _is_configured(self) -> bool:
        s = self._settings
        return bool(s and s.broker_host)

    def _build_client_kwargs(self, settings: object) -> dict[str, Any]:
        s: Any = settings
        kw: dict[str, Any] = {
            "hostname": s.broker_host,
            "port": s.broker_port,
            "username": s.username or None,
            "password": s.password or None,
        }
        if s.use_tls:
            ctx = ssl.create_default_context()
            if s.tls_insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            kw["tls_context"] = ctx
        return kw

    def _on_connected(self, settings: object) -> tuple[str, str]:
        s: Any = settings
        return ("HA MQTT connected", f"{s.broker_host}:{s.broker_port}")

    def _on_error(self) -> tuple[str, str]:
        return ("HA MQTT connection failure", "Please correct the settings or disable.")

    async def _on_connected_async(self, settings: object) -> None:
        if self._on_connected_callback:
            await self._on_connected_callback()


# ── Discovery config builders ─────────────────────────────────────────────


def _radio_discovery_configs(
    prefix: str,
    radio_key: str,
    radio_name: str,
) -> list[tuple[str, dict]]:
    """Build HA discovery config payloads for the local radio device."""
    nid = _node_id(radio_key)
    device = _device_payload(radio_key, radio_name, "Radio")
    state_topic = f"{prefix}/{nid}/health"
    configs: list[tuple[str, dict]] = []

    # binary_sensor: connected
    configs.append(
        (
            f"homeassistant/binary_sensor/meshcore_{nid}/connected/config",
            {
                "name": "Connected",
                "unique_id": f"meshcore_{nid}_connected",
                "device": device,
                "state_topic": state_topic,
                "value_template": "{{ 'ON' if value_json.connected else 'OFF' }}",
                "device_class": "connectivity",
                "payload_on": "ON",
                "payload_off": "OFF",
                "expire_after": 120,
            },
        )
    )

    # sensors from _RADIO_SENSORS (noise floor, battery, uptime, RSSI, etc.)
    for sensor in _RADIO_SENSORS:
        cfg: dict[str, Any] = {
            "name": sensor["name"],
            "unique_id": f"meshcore_{nid}_{sensor['object_id']}",
            "device": device,
            "state_topic": state_topic,
            "value_template": "{{ value_json." + sensor["field"] + " }}",  # type: ignore[operator]
            "expire_after": 120,
        }
        if sensor["device_class"]:
            cfg["device_class"] = sensor["device_class"]
        if sensor["state_class"]:
            cfg["state_class"] = sensor["state_class"]
        if sensor["unit"]:
            cfg["unit_of_measurement"] = sensor["unit"]
        if sensor.get("precision") is not None:
            cfg["suggested_display_precision"] = sensor["precision"]

        topic = f"homeassistant/sensor/meshcore_{nid}/{sensor['object_id']}/config"
        configs.append((topic, cfg))

    return configs


def _repeater_discovery_configs(
    prefix: str,
    pub_key: str,
    name: str,
    radio_key: str | None,
) -> list[tuple[str, dict]]:
    """Build HA discovery config payloads for a tracked repeater."""
    nid = _node_id(pub_key)
    device = _device_payload(pub_key, name, "Repeater", via_device_key=radio_key)
    state_topic = f"{prefix}/{nid}/telemetry"
    configs: list[tuple[str, dict]] = []

    for sensor in _REPEATER_SENSORS:
        cfg: dict[str, Any] = {
            "name": sensor["name"],
            "unique_id": f"meshcore_{nid}_{sensor['object_id']}",
            "device": device,
            "state_topic": state_topic,
            "value_template": "{{ value_json." + sensor["field"] + " }}",  # type: ignore[operator]
        }
        if sensor["device_class"]:
            cfg["device_class"] = sensor["device_class"]
        if sensor["state_class"]:
            cfg["state_class"] = sensor["state_class"]
        if sensor["unit"]:
            cfg["unit_of_measurement"] = sensor["unit"]
        if sensor.get("precision") is not None:
            cfg["suggested_display_precision"] = sensor["precision"]
        # 10 hours — margin over the 8-hour auto-collect cycle
        cfg["expire_after"] = 36000

        topic = f"homeassistant/sensor/meshcore_{nid}/{sensor['object_id']}/config"
        configs.append((topic, cfg))

    return configs


def _contact_tracker_discovery_config(
    prefix: str,
    pub_key: str,
    name: str,
    radio_key: str | None,
) -> tuple[str, dict]:
    """Build HA discovery config for a tracked contact's device_tracker."""
    nid = _node_id(pub_key)
    device = _device_payload(pub_key, name, "Node", via_device_key=radio_key)
    topic = f"homeassistant/device_tracker/meshcore_{nid}/config"
    cfg: dict[str, Any] = {
        "name": name or pub_key[:12],
        "unique_id": f"meshcore_{nid}_tracker",
        "device": device,
        "json_attributes_topic": f"{prefix}/{nid}/gps",
        "source_type": "gps",
    }
    return topic, cfg


def _message_event_discovery_config(
    prefix: str, radio_key: str, radio_name: str
) -> tuple[str, dict]:
    """Build HA discovery config for the message event entity."""
    nid = _node_id(radio_key)
    device = _device_payload(radio_key, radio_name, "Radio")
    topic = f"homeassistant/event/meshcore_{nid}/messages/config"
    cfg: dict[str, Any] = {
        "name": "Messages",
        "unique_id": f"meshcore_{nid}_messages",
        "device": device,
        "state_topic": f"{prefix}/{nid}/events/message",
        "event_types": ["message_received"],
    }
    return topic, cfg


# ── Module class ──────────────────────────────────────────────────────────


def _config_to_settings(config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        broker_host=config.get("broker_host", ""),
        broker_port=config.get("broker_port", 1883),
        username=config.get("username", ""),
        password=config.get("password", ""),
        use_tls=config.get("use_tls", False),
        tls_insecure=config.get("tls_insecure", False),
    )


class MqttHaModule(FanoutModule):
    """Home Assistant MQTT Discovery fanout module."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._publisher = _HaMqttPublisher()
        self._publisher.set_integration_name(name or config_id)
        self._publisher._on_connected_callback = self._publish_discovery
        self._discovery_topics: list[str] = []
        self._radio_key: str | None = None
        self._radio_name: str | None = None

    @property
    def _prefix(self) -> str:
        return self.config.get("topic_prefix", "meshcore")

    @property
    def _tracked_contacts(self) -> list[str]:
        return self.config.get("tracked_contacts") or []

    @property
    def _tracked_repeaters(self) -> list[str]:
        return self.config.get("tracked_repeaters") or []

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        self._seed_radio_identity_from_runtime()
        settings = _config_to_settings(self.config)
        await self._publisher.start(settings)

    async def stop(self) -> None:
        await self._remove_discovery()
        await self._publisher.stop()
        self._discovery_topics.clear()

    # ── Discovery publishing ──────────────────────────────────────────

    async def _publish_discovery(self) -> None:
        """Publish HA discovery configs and one-shot cached repeater state."""
        if not self._radio_key:
            # Don't publish discovery until we know the radio identity —
            # the first health heartbeat will provide it and trigger this.
            return

        configs: list[tuple[str, dict]] = []
        cached_repeater_states: list[tuple[str, dict[str, Any]]] = []

        radio_name = self._radio_name or "MeshCore Radio"
        configs.extend(_radio_discovery_configs(self._prefix, self._radio_key, radio_name))

        # Tracked repeaters — resolve names and LPP sensors from DB best-effort
        for pub_key in self._tracked_repeaters:
            rname = await self._resolve_contact_name(pub_key)
            configs.extend(
                _repeater_discovery_configs(self._prefix, pub_key, rname, self._radio_key)
            )
            latest = await self._resolve_latest_telemetry(pub_key)
            latest_data = latest.get("data", {}) if latest else {}
            # Dynamic LPP sensor entities from last known telemetry snapshot
            lpp_sensors = latest_data.get("lpp_sensors", [])
            if lpp_sensors:
                nid = _node_id(pub_key)
                device = _device_payload(pub_key, rname, "Repeater", via_device_key=self._radio_key)
                state_topic = f"{self._prefix}/{nid}/telemetry"
                configs.extend(
                    _lpp_discovery_configs(self._prefix, pub_key, device, lpp_sensors, state_topic)
                )
            if latest_data:
                cached_repeater_states.append(
                    (
                        f"{self._prefix}/{_node_id(pub_key)}/telemetry",
                        _repeater_telemetry_payload(latest_data),
                    )
                )

        # Tracked contacts — resolve names and LPP sensors from DB best-effort
        for pub_key in self._tracked_contacts:
            cname = await self._resolve_contact_name(pub_key)
            configs.append(
                _contact_tracker_discovery_config(self._prefix, pub_key, cname, self._radio_key)
            )
            # LPP sensor entities for contacts with telemetry history
            latest_ct = await self._resolve_latest_contact_telemetry(pub_key)
            latest_ct_data = latest_ct.get("data", {}) if latest_ct else {}
            ct_lpp_sensors = latest_ct_data.get("lpp_sensors", [])
            if ct_lpp_sensors:
                ct_nid = _node_id(pub_key)
                ct_device = _device_payload(pub_key, cname, "Node", via_device_key=self._radio_key)
                ct_state_topic = f"{self._prefix}/{ct_nid}/telemetry"
                configs.extend(
                    _lpp_discovery_configs(
                        self._prefix, pub_key, ct_device, ct_lpp_sensors, ct_state_topic
                    )
                )
            if latest_ct_data:
                ct_payload = _contact_telemetry_payload(latest_ct_data)
                cached_repeater_states.append(
                    (f"{self._prefix}/{_node_id(pub_key)}/telemetry", ct_payload)
                )

        # Message event entity (namespaced to this radio)
        configs.append(_message_event_discovery_config(self._prefix, self._radio_key, radio_name))

        self._discovery_topics = [topic for topic, _ in configs]

        for topic, payload in configs:
            await self._publisher.publish(topic, payload, retain=True)

        for topic, payload in cached_repeater_states:
            # Replay cached state after discovery so newly created HA entities
            # populate immediately, but do not retain it or HA will treat a
            # broker reconnect as fresh telemetry and reset expire_after.
            await self._publisher.publish(topic, payload)

        logger.info(
            "HA MQTT: published %d discovery configs (%d repeaters, %d contacts, %d cached telemetry states)",
            len(configs),
            len(self._tracked_repeaters),
            len(self._tracked_contacts),
            len(cached_repeater_states),
        )

    async def _clear_retained_topics(self, topics: list[str]) -> None:
        """Publish empty retained payloads to remove entries from broker."""
        for topic in topics:
            try:
                if self._publisher._client:
                    await self._publisher._client.publish(topic, b"", retain=True)
            except Exception:
                pass  # best-effort cleanup

    async def _remove_discovery(self) -> None:
        """Publish empty retained payloads to remove all HA entities."""
        if not self._publisher.connected or not self._discovery_topics:
            return
        await self._clear_retained_topics(self._discovery_topics)

    @staticmethod
    async def _resolve_contact_name(pub_key: str) -> str:
        """Look up a contact's display name, falling back to 12-char prefix."""
        try:
            from app.repository.contacts import ContactRepository

            contact = await ContactRepository.get_by_key(pub_key)
            if contact and contact.name:
                return contact.name
        except Exception:
            pass
        return pub_key[:12]

    @staticmethod
    async def _resolve_latest_telemetry(pub_key: str) -> dict | None:
        """Return the most recent telemetry row for a repeater, or None."""
        try:
            from app.repository.repeater_telemetry import RepeaterTelemetryRepository

            return await RepeaterTelemetryRepository.get_latest(pub_key)
        except Exception:
            pass
        return None

    @staticmethod
    async def _resolve_latest_contact_telemetry(pub_key: str) -> dict | None:
        """Return the most recent contact telemetry row, or None."""
        try:
            from app.repository.contact_telemetry import ContactTelemetryRepository

            return await ContactTelemetryRepository.get_latest(pub_key)
        except Exception:
            pass
        return None

    def _seed_radio_identity_from_runtime(self) -> None:
        """Best-effort bootstrap from the currently connected radio session."""
        try:
            from app.services.radio_runtime import radio_runtime

            if not radio_runtime.is_connected:
                return

            mc = radio_runtime.meshcore
            self_info = mc.self_info if mc is not None else None
            if not isinstance(self_info, dict):
                return

            pub_key = self_info.get("public_key")
            if isinstance(pub_key, str) and pub_key.strip():
                self._radio_key = pub_key.strip().lower()

            name = self_info.get("name")
            if isinstance(name, str) and name.strip():
                self._radio_name = name.strip()
        except Exception:
            logger.debug("HA MQTT: failed to seed radio identity from runtime", exc_info=True)

    # ── Event handlers ────────────────────────────────────────────────

    async def on_health(self, data: dict) -> None:
        if not self._publisher.connected:
            return

        # Cache radio identity for discovery config generation
        pub_key = data.get("public_key")
        if pub_key:
            new_name = data.get("name")
            key_changed = pub_key != self._radio_key
            name_changed = new_name and new_name != self._radio_name

            if key_changed:
                old_key = self._radio_key
                old_topics = list(self._discovery_topics)
                if old_topics:
                    await self._clear_retained_topics(old_topics)
                    self._discovery_topics.clear()
                self._radio_key = pub_key
                self._radio_name = new_name
                # Remove stale discovery entries from the old identity (e.g.
                # "unknown" placeholder from before the radio key was known),
                # then re-publish with the real identity.
                if old_key is not None and not old_topics:
                    await self._clear_retained_topics(
                        [t for t, _ in _radio_discovery_configs(self._prefix, old_key, "")]
                    )
                await self._publish_discovery()
            elif name_changed:
                self._radio_name = new_name
                await self._publish_discovery()

        # Don't publish health state until we know the radio identity —
        # otherwise we create a stale "unknown" device in HA.
        if not self._radio_key:
            return

        nid = _node_id(self._radio_key)
        payload: dict[str, Any] = {"connected": data.get("connected", False)}
        for sensor in _RADIO_SENSORS:
            field = sensor["field"]
            if field is not None:
                payload[field] = data.get(field)

        # Normalize battery from millivolts to volts for consistency with
        # repeater battery and the discovery config (unit: V, precision: 2).
        battery_mv = data.get("battery_mv")
        if battery_mv is not None:
            payload["battery_volts"] = battery_mv / 1000.0

        await self._publisher.publish(f"{self._prefix}/{nid}/health", payload)

    async def on_contact(self, data: dict) -> None:
        if not self._publisher.connected:
            return

        pub_key = data.get("public_key", "")
        if pub_key not in self._tracked_contacts:
            return

        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
            return

        nid = _node_id(pub_key)
        await self._publisher.publish(
            f"{self._prefix}/{nid}/gps",
            {
                "latitude": lat,
                "longitude": lon,
                "gps_accuracy": 0,
                "source_type": "gps",
            },
        )

    async def on_telemetry(self, data: dict) -> None:
        if not self._publisher.connected:
            return

        pub_key = data.get("public_key", "")
        if pub_key not in self._tracked_repeaters and pub_key not in self._tracked_contacts:
            return

        nid = _node_id(pub_key)
        is_repeater = pub_key in self._tracked_repeaters
        payload = (
            _repeater_telemetry_payload(data) if is_repeater else _contact_telemetry_payload(data)
        )
        lpp_sensors: list[dict] = data.get("lpp_sensors", [])
        rediscover = False
        for _, key, _ in _assign_lpp_keys(lpp_sensors):
            expected_topic = f"homeassistant/sensor/meshcore_{nid}/{key}/config"
            if expected_topic not in self._discovery_topics:
                rediscover = True

        # If new LPP sensor types appeared, re-publish discovery *before*
        # the state payload so HA already knows the entity when the value arrives.
        if rediscover:
            await self._publish_discovery()

        await self._publisher.publish(f"{self._prefix}/{nid}/telemetry", payload)

    async def on_message(self, data: dict) -> None:
        if not self._publisher.connected or not self._radio_key:
            return

        text = get_fanout_message_text(data)
        nid = _node_id(self._radio_key)
        await self._publisher.publish(
            f"{self._prefix}/{nid}/events/message",
            {
                "event_type": "message_received",
                "sender_name": data.get("sender_name", ""),
                "sender_key": data.get("sender_key", ""),
                "text": text,
                "conversation_key": data.get("conversation_key", ""),
                "message_type": data.get("type", ""),
                "channel_name": data.get("channel_name"),
                "outgoing": data.get("outgoing", False),
            },
        )

    # ── Status ────────────────────────────────────────────────────────

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
