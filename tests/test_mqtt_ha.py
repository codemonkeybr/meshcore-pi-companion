"""Tests for the Home Assistant MQTT Discovery fanout module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.fanout.mqtt_ha import (
    MqttHaModule,
    _assign_lpp_keys,
    _contact_tracker_discovery_config,
    _device_payload,
    _lpp_discovery_configs,
    _lpp_sensor_key,
    _message_event_discovery_config,
    _node_id,
    _radio_discovery_configs,
    _repeater_discovery_configs,
    _repeater_telemetry_payload,
)

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _base_config(**overrides) -> dict:
    cfg = {
        "broker_host": "127.0.0.1",
        "broker_port": 1883,
        "username": "",
        "password": "",
        "use_tls": False,
        "tls_insecure": False,
        "topic_prefix": "meshcore",
        "tracked_contacts": [],
        "tracked_repeaters": [],
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Unit: node_id and device_payload
# ---------------------------------------------------------------------------


class TestNodeId:
    def test_returns_12_char_prefix(self):
        assert _node_id("aabbccddeeff11223344") == "aabbccddeeff"

    def test_lowercases(self):
        assert _node_id("AABBCCDDEEFF") == "aabbccddeeff"


class TestDevicePayload:
    def test_basic(self):
        dev = _device_payload("aabbccddeeff1122", "MyRadio", "Radio")
        assert dev["identifiers"] == ["meshcore_aabbccddeeff"]
        assert dev["name"] == "MyRadio"
        assert dev["manufacturer"] == "MeshCore"
        assert dev["model"] == "Radio"
        assert "via_device" not in dev

    def test_via_device(self):
        dev = _device_payload("ccdd", "Repeater1", "Repeater", via_device_key="aabb")
        assert dev["via_device"] == "meshcore_aabb"

    def test_name_fallback(self):
        dev = _device_payload("aabbccddeeff", "", "Radio")
        assert dev["name"] == "aabbccddeeff"


# ---------------------------------------------------------------------------
# Unit: discovery config builders
# ---------------------------------------------------------------------------


class TestRadioDiscovery:
    def test_produces_discovery_configs(self):
        configs = _radio_discovery_configs("meshcore", "aabbccddeeff1122", "MyRadio")
        # 1 binary_sensor (connected) + 9 sensors from _RADIO_SENSORS
        assert len(configs) == 10

        topics = [t for t, _ in configs]
        assert "homeassistant/binary_sensor/meshcore_aabbccddeeff/connected/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/noise_floor/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/battery/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/uptime/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/last_rssi/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/last_snr/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/packets_received/config" in topics
        assert "homeassistant/sensor/meshcore_aabbccddeeff/packets_sent/config" in topics

    def test_connected_binary_sensor_shape(self):
        configs = _radio_discovery_configs("mc", "aabbccddeeff", "R")
        topic, cfg = configs[0]
        assert cfg["device_class"] == "connectivity"
        assert cfg["state_topic"] == "mc/aabbccddeeff/health"
        assert cfg["unique_id"] == "meshcore_aabbccddeeff_connected"
        assert cfg["expire_after"] == 120

    def test_sensor_configs_have_expire_after(self):
        configs = _radio_discovery_configs("mc", "aabbccddeeff", "R")
        # All sensor configs (skip the binary_sensor at index 0)
        for _, cfg in configs[1:]:
            assert cfg["expire_after"] == 120

    def test_sensor_configs_have_display_precision(self):
        configs = _radio_discovery_configs("mc", "aabbccddeeff", "R")
        # All sensor configs (skip the binary_sensor at index 0)
        for _, cfg in configs[1:]:
            assert "suggested_display_precision" in cfg
            assert isinstance(cfg["suggested_display_precision"], int)

    def test_battery_sensor_uses_volts(self):
        configs = _radio_discovery_configs("mc", "aabbccddeeff", "R")
        battery_cfgs = [(t, c) for t, c in configs if "battery" in t]
        assert len(battery_cfgs) == 1
        _, cfg = battery_cfgs[0]
        assert cfg["unit_of_measurement"] == "V"
        assert cfg["suggested_display_precision"] == 2


class TestRepeaterDiscovery:
    def test_produces_sensor_per_field(self):
        configs = _repeater_discovery_configs("mc", "ccdd11223344", "Rep1", "aabb")
        assert len(configs) == 8  # matches _REPEATER_SENSORS length

        topics = [t for t, _ in configs]
        assert "homeassistant/sensor/meshcore_ccdd11223344/battery_voltage/config" in topics
        assert "homeassistant/sensor/meshcore_ccdd11223344/uptime/config" in topics

    def test_via_device_set(self):
        configs = _repeater_discovery_configs("mc", "ccdd", "Rep1", "aabb")
        _, cfg = configs[0]
        assert cfg["device"]["via_device"] == "meshcore_aabb"

    def test_sensors_have_expire_after(self):
        configs = _repeater_discovery_configs("mc", "ccdd", "Rep1", None)
        for _, cfg in configs:
            assert cfg["expire_after"] == 36000

    def test_sensors_have_display_precision(self):
        configs = _repeater_discovery_configs("mc", "ccdd", "Rep1", None)
        for _, cfg in configs:
            assert "suggested_display_precision" in cfg


class TestContactTrackerDiscovery:
    def test_config_shape(self):
        topic, cfg = _contact_tracker_discovery_config("mc", "eeff11223344", "Alice", "aabb")
        assert topic == "homeassistant/device_tracker/meshcore_eeff11223344/config"
        assert cfg["unique_id"] == "meshcore_eeff11223344_tracker"
        assert cfg["source_type"] == "gps"
        assert cfg["json_attributes_topic"] == "mc/eeff11223344/gps"
        assert "state_topic" not in cfg


class TestMessageEventDiscovery:
    def test_config_shape(self):
        topic, cfg = _message_event_discovery_config("mc", "aabbccddeeff", "MyRadio")
        assert topic == "homeassistant/event/meshcore_aabbccddeeff/messages/config"
        assert "message_received" in cfg["event_types"]
        assert cfg["state_topic"] == "mc/aabbccddeeff/events/message"
        assert cfg["unique_id"] == "meshcore_aabbccddeeff_messages"
        assert cfg["device"]["identifiers"] == ["meshcore_aabbccddeeff"]


# ---------------------------------------------------------------------------
# Module: filtering
# ---------------------------------------------------------------------------


class TestMqttHaFiltering:
    @pytest.mark.asyncio
    async def test_on_contact_ignores_untracked(self):
        mod = MqttHaModule("test", _base_config(tracked_contacts=["aaaa"]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_contact({"public_key": "bbbb", "lat": 1.0, "lon": 2.0})

        mod._publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_contact_publishes_tracked(self):
        key = "aabbccddeeff"
        mod = MqttHaModule("test", _base_config(tracked_contacts=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_contact({"public_key": key, "lat": 37.7, "lon": -122.4})

        mod._publisher.publish.assert_called_once()
        topic = mod._publisher.publish.call_args[0][0]
        payload = mod._publisher.publish.call_args[0][1]
        assert topic == f"meshcore/{_node_id(key)}/gps"
        assert payload["latitude"] == 37.7
        assert payload["longitude"] == -122.4

    @pytest.mark.asyncio
    async def test_on_contact_skips_zero_gps(self):
        key = "aabbccddeeff"
        mod = MqttHaModule("test", _base_config(tracked_contacts=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_contact({"public_key": key, "lat": 0.0, "lon": 0.0})

        mod._publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_telemetry_ignores_untracked(self):
        mod = MqttHaModule("test", _base_config(tracked_repeaters=["aaaa"]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_telemetry({"public_key": "bbbb", "battery_volts": 4.1})

        mod._publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_telemetry_publishes_tracked(self):
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "noise_floor_dbm": -112,
                "last_rssi_dbm": -80,
                "last_snr_db": 10.0,
                "packets_received": 500,
                "packets_sent": 300,
                "uptime_seconds": 86400,
            }
        )

        mod._publisher.publish.assert_called_once()
        topic = mod._publisher.publish.call_args[0][0]
        payload = mod._publisher.publish.call_args[0][1]
        assert topic == f"meshcore/{_node_id(key)}/telemetry"
        assert payload["battery_volts"] == 4.1
        assert payload["uptime_seconds"] == 86400
        assert mod._publisher.publish.call_args.kwargs.get("retain") is not True


class TestMqttHaHealth:
    @pytest.mark.asyncio
    async def test_on_health_publishes_state(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._radio_key = "aabbccddeeff"

        await mod.on_health(
            {
                "connected": True,
                "public_key": "aabbccddeeff",
                "name": "MyRadio",
                "noise_floor_dbm": -110,
                "battery_mv": 4150,
                "uptime_secs": 3600,
                "last_rssi": -85,
                "last_snr": 9.5,
                "packets_recv": 500,
                "packets_sent": 250,
            }
        )

        # Should publish health state
        calls = mod._publisher.publish.call_args_list
        # Last call should be health state (discovery may also be published)
        health_calls = [c for c in calls if "/health" in c[0][0]]
        assert len(health_calls) >= 1
        payload = health_calls[-1][0][1]
        assert payload["connected"] is True
        assert payload["noise_floor_dbm"] == -110
        assert payload["battery_volts"] == 4.15
        assert payload["uptime_secs"] == 3600
        assert payload["last_rssi"] == -85
        assert payload["packets_recv"] == 500
        assert payload["packets_sent"] == 250

    @pytest.mark.asyncio
    async def test_on_health_caches_radio_key(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_health(
            {
                "connected": True,
                "public_key": "aabbccddeeff",
                "name": "MyRadio",
                "noise_floor_dbm": None,
            }
        )

        assert mod._radio_key == "aabbccddeeff"
        assert mod._radio_name == "MyRadio"

    @pytest.mark.asyncio
    async def test_on_health_key_change_clears_all_existing_discovery_topics(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._radio_key = "aabbccddeeff"
        mod._radio_name = "OldRadio"
        mod._discovery_topics = [
            "homeassistant/sensor/meshcore_aabbccddeeff/noise_floor/config",
            "homeassistant/event/meshcore_aabbccddeeff/messages/config",
            "homeassistant/device_tracker/meshcore_ccdd11223344/config",
            "homeassistant/sensor/meshcore_eeff11223344/battery_voltage/config",
        ]

        mod._clear_retained_topics = AsyncMock()

        async def publish_discovery_side_effect():
            assert mod._discovery_topics == []
            mod._discovery_topics = [
                "homeassistant/sensor/meshcore_112233445566/noise_floor/config",
                "homeassistant/event/meshcore_112233445566/messages/config",
            ]

        mod._publish_discovery = AsyncMock(side_effect=publish_discovery_side_effect)

        await mod.on_health(
            {
                "connected": True,
                "public_key": "112233445566",
                "name": "NewRadio",
            }
        )

        mod._clear_retained_topics.assert_awaited_once_with(
            [
                "homeassistant/sensor/meshcore_aabbccddeeff/noise_floor/config",
                "homeassistant/event/meshcore_aabbccddeeff/messages/config",
                "homeassistant/device_tracker/meshcore_ccdd11223344/config",
                "homeassistant/sensor/meshcore_eeff11223344/battery_voltage/config",
            ]
        )
        mod._publish_discovery.assert_awaited_once()
        assert mod._radio_key == "112233445566"
        assert mod._radio_name == "NewRadio"
        assert mod._discovery_topics == [
            "homeassistant/sensor/meshcore_112233445566/noise_floor/config",
            "homeassistant/event/meshcore_112233445566/messages/config",
        ]


class TestMqttHaLifecycle:
    @pytest.mark.asyncio
    async def test_start_seeds_radio_identity_from_connected_runtime(self, monkeypatch):
        from app.services.radio_runtime import radio_runtime

        monkeypatch.setattr(
            radio_runtime.manager,
            "_meshcore",
            SimpleNamespace(
                is_connected=True,
                self_info={"public_key": "AABBCCDDEEFF", "name": "MyRadio"},
            ),
        )

        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.start = AsyncMock()

        await mod.start()

        assert mod._radio_key == "aabbccddeeff"
        assert mod._radio_name == "MyRadio"
        mod._publisher.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_discovery_replays_cached_repeater_telemetry_after_configs(self):
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.publish = AsyncMock()
        mod._radio_key = "aabbccddeeff"
        mod._radio_name = "MyRadio"

        latest = {
            "timestamp": 1234,
            "data": {
                "battery_volts": 4.1,
                "noise_floor_dbm": -112,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 23.5},
                ],
            },
        }

        mod._resolve_contact_name = AsyncMock(return_value="Rep1")
        mod._resolve_latest_telemetry = AsyncMock(return_value=latest)

        await mod._publish_discovery()

        calls = mod._publisher.publish.call_args_list
        discovery_calls = [c for c in calls if c.args[0].startswith("homeassistant/")]
        telemetry_calls = [c for c in calls if c.args[0] == f"meshcore/{_node_id(key)}/telemetry"]

        assert telemetry_calls
        assert telemetry_calls[-1].args[1] == _repeater_telemetry_payload(latest["data"])
        assert telemetry_calls[-1].kwargs.get("retain") is not True
        assert calls.index(telemetry_calls[-1]) > calls.index(discovery_calls[-1])


class TestMqttHaMessage:
    @pytest.mark.asyncio
    async def test_on_message_publishes_event(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._radio_key = "aabbccddeeff"

        await mod.on_message(
            {
                "type": "PRIV",
                "conversation_key": "pk1",
                "text": "hello",
                "sender_name": "Alice",
                "sender_key": "aabb",
                "outgoing": False,
            }
        )

        mod._publisher.publish.assert_called_once()
        topic = mod._publisher.publish.call_args[0][0]
        payload = mod._publisher.publish.call_args[0][1]
        assert topic == "meshcore/aabbccddeeff/events/message"
        assert payload["event_type"] == "message_received"
        assert payload["text"] == "hello"
        assert payload["sender_name"] == "Alice"
        assert payload["outgoing"] is False

    @pytest.mark.asyncio
    async def test_on_message_skips_without_radio_key(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        # _radio_key is None — should not publish
        await mod.on_message({"type": "PRIV", "text": "hi", "sender_name": "X"})
        mod._publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_strips_channel_sender_prefix(self):
        mod = MqttHaModule("test", _base_config())
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._radio_key = "aabbccddeeff"

        await mod.on_message(
            {
                "type": "CHAN",
                "conversation_key": "ch1",
                "text": "Alice: hello from channel",
                "sender_name": "Alice",
                "sender_key": "aabb",
                "outgoing": False,
            }
        )

        payload = mod._publisher.publish.call_args[0][1]
        assert payload["text"] == "hello from channel"


class TestMqttHaStatus:
    def test_disconnected_without_host(self):
        mod = MqttHaModule("test", _base_config(broker_host=""))
        assert mod.status == "disconnected"

    def test_disconnected_with_host_but_not_connected(self):
        mod = MqttHaModule("test", _base_config(broker_host="192.168.1.1"))
        assert mod.status == "disconnected"

    def test_error_when_publisher_has_error(self):
        mod = MqttHaModule("test", _base_config(broker_host="192.168.1.1"))
        mod._publisher._last_error = "connection refused"
        assert mod.status == "error"


# ---------------------------------------------------------------------------
# Router validation
# ---------------------------------------------------------------------------


class TestMqttHaValidation:
    def test_valid_config_passes(self):
        from app.routers.fanout import _validate_mqtt_ha_config

        _validate_mqtt_ha_config({"broker_host": "192.168.1.1", "broker_port": 1883})

    def test_missing_host_fails(self):
        from app.routers.fanout import _validate_mqtt_ha_config

        with pytest.raises(Exception, match="broker_host"):
            _validate_mqtt_ha_config({"broker_host": ""})

    def test_bad_port_fails(self):
        from app.routers.fanout import _validate_mqtt_ha_config

        with pytest.raises(Exception, match="broker_port"):
            _validate_mqtt_ha_config({"broker_host": "x", "broker_port": 99999})

    def test_tracked_contacts_must_be_list(self):
        from app.routers.fanout import _validate_mqtt_ha_config

        with pytest.raises(Exception, match="tracked_contacts"):
            _validate_mqtt_ha_config(
                {
                    "broker_host": "x",
                    "tracked_contacts": "not-a-list",
                }
            )

    def test_scope_enforced_no_raw_packets(self):
        from app.routers.fanout import _enforce_scope

        result = _enforce_scope("mqtt_ha", {"messages": "all", "raw_packets": "all"})
        assert result["raw_packets"] == "none"
        assert result["messages"] == "all"


# ---------------------------------------------------------------------------
# LPP sensor discovery and telemetry
# ---------------------------------------------------------------------------


class TestLppSensorKey:
    def test_basic(self):
        assert _lpp_sensor_key("temperature", 1) == "lpp_temperature_ch1"

    def test_zero_channel(self):
        assert _lpp_sensor_key("humidity", 0) == "lpp_humidity_ch0"


class TestAssignLppKeys:
    def test_no_duplicates(self):
        sensors = [
            {"type_name": "temperature", "channel": 1, "value": 20},
            {"type_name": "humidity", "channel": 2, "value": 45},
        ]
        result = _assign_lpp_keys(sensors)
        assert [(k, n) for _, k, n in result] == [
            ("lpp_temperature_ch1", 1),
            ("lpp_humidity_ch2", 1),
        ]

    def test_duplicate_type_and_channel(self):
        sensors = [
            {"type_name": "temperature", "channel": 1, "value": 20},
            {"type_name": "humidity", "channel": 2, "value": 45},
            {"type_name": "temperature", "channel": 1, "value": 53},
        ]
        result = _assign_lpp_keys(sensors)
        assert [(k, n) for _, k, n in result] == [
            ("lpp_temperature_ch1", 1),
            ("lpp_humidity_ch2", 1),
            ("lpp_temperature_ch1_2", 2),
        ]

    def test_triple_duplicate(self):
        sensors = [
            {"type_name": "voltage", "channel": 0, "value": 3.3},
            {"type_name": "voltage", "channel": 0, "value": 5.0},
            {"type_name": "voltage", "channel": 0, "value": 12.0},
        ]
        result = _assign_lpp_keys(sensors)
        keys = [k for _, k, _ in result]
        assert keys == ["lpp_voltage_ch0", "lpp_voltage_ch0_2", "lpp_voltage_ch0_3"]

    def test_empty_list(self):
        assert _assign_lpp_keys([]) == []


class TestLppDiscoveryConfigs:
    def test_produces_config_per_sensor(self):
        nid = "ccdd11223344"
        device = _device_payload(nid, "Rep1", "Repeater")
        sensors = [
            {"channel": 1, "type_name": "temperature", "value": 23.5},
            {"channel": 2, "type_name": "humidity", "value": 45.0},
        ]
        configs = _lpp_discovery_configs("mc", nid, device, sensors, f"mc/{nid}/telemetry")

        assert len(configs) == 2
        topics = [t for t, _ in configs]
        assert f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1/config" in topics
        assert f"homeassistant/sensor/meshcore_{nid}/lpp_humidity_ch2/config" in topics

    def test_sensor_config_shape(self):
        nid = "ccdd11223344"
        device = _device_payload(nid, "Rep1", "Repeater")
        sensors = [{"channel": 1, "type_name": "temperature", "value": 23.5}]
        configs = _lpp_discovery_configs("mc", nid, device, sensors, f"mc/{nid}/telemetry")

        _, cfg = configs[0]
        assert cfg["name"] == "Temperature (Ch 1)"
        assert cfg["unique_id"] == f"meshcore_{nid}_lpp_temperature_ch1"
        assert cfg["device_class"] == "temperature"
        assert cfg["unit_of_measurement"] == "°C"
        assert cfg["state_class"] == "measurement"
        assert cfg["expire_after"] == 36000
        assert cfg["suggested_display_precision"] == 1
        assert "lpp_temperature_ch1" in cfg["value_template"]

    def test_duplicate_type_channel_gets_indexed_keys(self):
        nid = "ccdd11223344"
        device = _device_payload(nid, "Rep1", "Repeater")
        sensors = [
            {"channel": 1, "type_name": "temperature", "value": 20.0},
            {"channel": 2, "type_name": "humidity", "value": 45.0},
            {"channel": 1, "type_name": "temperature", "value": 53.0},
        ]
        configs = _lpp_discovery_configs("mc", nid, device, sensors, f"mc/{nid}/telemetry")

        assert len(configs) == 3
        topics = [t for t, _ in configs]
        assert f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1/config" in topics
        assert f"homeassistant/sensor/meshcore_{nid}/lpp_humidity_ch2/config" in topics
        assert f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1_2/config" in topics

        # First temperature keeps base name, second gets #2 suffix
        names = {cfg["unique_id"]: cfg["name"] for _, cfg in configs}
        assert names[f"meshcore_{nid}_lpp_temperature_ch1"] == "Temperature (Ch 1)"
        assert names[f"meshcore_{nid}_lpp_temperature_ch1_2"] == "Temperature (Ch 1) #2"

    def test_unknown_sensor_type_no_device_class(self):
        nid = "ccdd11223344"
        device = _device_payload(nid, "Rep1", "Repeater")
        sensors = [{"channel": 0, "type_name": "exotic_sensor", "value": 1.0}]
        configs = _lpp_discovery_configs("mc", nid, device, sensors, f"mc/{nid}/telemetry")

        _, cfg = configs[0]
        assert "device_class" not in cfg
        assert "unit_of_measurement" not in cfg


class TestMqttHaTelemetryWithLpp:
    @pytest.mark.asyncio
    async def test_on_telemetry_flattens_lpp_sensors(self):
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        # Pretend discovery already covers these sensors
        nid = _node_id(key)
        mod._discovery_topics = [
            f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1/config",
            f"homeassistant/sensor/meshcore_{nid}/lpp_humidity_ch2/config",
        ]

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 23.5},
                    {"channel": 2, "type_name": "humidity", "value": 45.0},
                ],
            }
        )

        mod._publisher.publish.assert_called_once()
        payload = mod._publisher.publish.call_args[0][1]
        assert payload["battery_volts"] == 4.1
        assert payload["lpp_temperature_ch1"] == 23.5
        assert payload["lpp_humidity_ch2"] == 45.0
        assert mod._publisher.publish.call_args.kwargs.get("retain") is not True

    @pytest.mark.asyncio
    async def test_on_telemetry_triggers_rediscovery_for_new_lpp_sensor(self):
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._discovery_topics = []  # No sensors discovered yet
        mod._publish_discovery = AsyncMock()

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 23.5},
                ],
            }
        )

        mod._publish_discovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_telemetry_discovery_published_before_state(self):
        """Discovery configs must arrive before the state payload so HA knows the entity."""
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._discovery_topics = []  # New sensor triggers rediscovery

        call_order: list[str] = []

        async def fake_discovery():
            call_order.append("discovery")

        mod._publish_discovery = AsyncMock(side_effect=fake_discovery)

        original_publish = mod._publisher.publish

        async def tracking_publish(topic, payload, **kw):
            if "/telemetry" in topic:
                call_order.append("state")
            return await original_publish(topic, payload, **kw)

        mod._publisher.publish = AsyncMock(side_effect=tracking_publish)

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 23.5},
                ],
            }
        )

        assert call_order == ["discovery", "state"]

    @pytest.mark.asyncio
    async def test_on_telemetry_no_rediscovery_when_already_known(self):
        key = "ccdd11223344"
        nid = _node_id(key)
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._discovery_topics = [
            f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1/config",
        ]
        mod._publish_discovery = AsyncMock()

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 23.5},
                ],
            }
        )

        mod._publish_discovery.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_telemetry_duplicate_lpp_sensors_not_overwritten(self):
        """Two sensors with same (type_name, channel) get distinct keys."""
        key = "ccdd11223344"
        nid = _node_id(key)
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()
        mod._discovery_topics = [
            f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1/config",
            f"homeassistant/sensor/meshcore_{nid}/lpp_temperature_ch1_2/config",
        ]

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "lpp_sensors": [
                    {"channel": 1, "type_name": "temperature", "value": 20.0},
                    {"channel": 1, "type_name": "temperature", "value": 53.0},
                ],
            }
        )

        payload = mod._publisher.publish.call_args[0][1]
        assert payload["lpp_temperature_ch1"] == 20.0
        assert payload["lpp_temperature_ch1_2"] == 53.0

    @pytest.mark.asyncio
    async def test_on_telemetry_without_lpp_sensors(self):
        """Existing behavior: no lpp_sensors key means no LPP fields in payload."""
        key = "ccdd11223344"
        mod = MqttHaModule("test", _base_config(tracked_repeaters=[key]))
        mod._publisher = MagicMock()
        mod._publisher.connected = True
        mod._publisher.publish = AsyncMock()

        await mod.on_telemetry(
            {
                "public_key": key,
                "battery_volts": 4.1,
                "noise_floor_dbm": -112,
            }
        )

        payload = mod._publisher.publish.call_args[0][1]
        assert payload["battery_volts"] == 4.1
        # No lpp keys
        assert not any(k.startswith("lpp_") for k in payload)
