"""Tests for the SPI backend and its supporting modules.

These tests run without real SPI hardware by mocking pymc_core's radio layer.
They verify:
  - Hardware profile lookup
  - Identity generation/persistence
  - Contact store adapter
  - Channel DB adapter
  - SpiBackend event bridge and RadioBackend method contracts
  - Config transport exclusivity with SPI
  - Integration: packet pipeline with SPI backend (mock radio, real DB)
"""

import asyncio
import base64
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load shared packet pipeline fixtures for integration tests
_FIXTURES_PATH = Path(__file__).parent / "fixtures" / "websocket_events.json"
if _FIXTURES_PATH.exists():
    with open(_FIXTURES_PATH, encoding="utf-8") as f:
        _PIPELINE_FIXTURES = json.load(f)
else:
    _PIPELINE_FIXTURES = {}

# ---------------------------------------------------------------------------
# Hardware profiles
# ---------------------------------------------------------------------------


class TestHardwareProfiles:
    def test_get_known_profile(self):
        from app.backends.spi_config import get_profile

        p = get_profile("waveshare")
        assert p.name == "Waveshare LoRa HAT (SPI)"
        assert p.is_waveshare is True
        assert p.bus_id == 0
        assert p.cs_pin == 21

    def test_get_unknown_profile_raises(self):
        from app.backends.spi_config import get_profile

        with pytest.raises(ValueError, match="Unknown hardware profile"):
            get_profile("does-not-exist")

    def test_all_profiles_have_required_fields(self):
        from app.backends.spi_config import HARDWARE_PROFILES

        for name, profile in HARDWARE_PROFILES.items():
            assert profile.name, f"Profile {name} has no name"
            assert isinstance(profile.bus_id, int)
            assert isinstance(profile.reset_pin, int)
            assert isinstance(profile.busy_pin, int)
            assert isinstance(profile.irq_pin, int)

    def test_uconsole_uses_spi1(self):
        from app.backends.spi_config import get_profile

        p = get_profile("uconsole")
        assert p.bus_id == 1


# ---------------------------------------------------------------------------
# Identity management
# ---------------------------------------------------------------------------


class TestSpiIdentity:
    def test_generate_identity_seed_length(self):
        from app.spi_identity import generate_identity_seed

        seed = generate_identity_seed()
        assert len(seed) == 32
        # Each call should produce different output
        assert generate_identity_seed() != seed

    def test_load_or_create_identity_creates_new(self, tmp_path):
        from app.spi_identity import load_or_create_identity, load_spi_config

        cfg_path = tmp_path / "spi_config.json"
        seed = load_or_create_identity(cfg_path)
        assert len(seed) == 32

        # Should be persisted
        config = load_spi_config(cfg_path)
        assert "identity_key" in config
        assert base64.b64decode(config["identity_key"]) == seed

    def test_load_or_create_identity_loads_existing(self, tmp_path):
        from app.spi_identity import load_or_create_identity, save_spi_config

        cfg_path = tmp_path / "spi_config.json"
        original_seed = os.urandom(32)
        save_spi_config(
            {"identity_key": base64.b64encode(original_seed).decode()},
            cfg_path,
        )

        loaded = load_or_create_identity(cfg_path)
        assert loaded == original_seed

    def test_import_identity_validates_length(self, tmp_path):
        from app.spi_identity import import_identity

        cfg_path = tmp_path / "spi_config.json"
        with pytest.raises(ValueError, match="32 bytes"):
            import_identity(b"too-short", cfg_path)

    def test_export_identity_round_trip(self, tmp_path):
        from app.spi_identity import export_identity, import_identity

        cfg_path = tmp_path / "spi_config.json"
        seed = os.urandom(32)
        import_identity(seed, cfg_path)
        assert export_identity(cfg_path) == seed


# ---------------------------------------------------------------------------
# Contact store adapter
# ---------------------------------------------------------------------------


class TestSpiContactStore:
    def test_get_by_name(self):
        from app.backends.spi_contact_store import SpiContactStore

        store = SpiContactStore()
        store.add_or_update("aabb" * 8, "Alice")
        assert store.get_by_name("Alice") is not None
        assert store.get_by_name("Alice").public_key == "aabb" * 8
        assert store.get_by_name("Bob") is None

    def test_contacts_iterable(self):
        from app.backends.spi_contact_store import SpiContactStore

        store = SpiContactStore()
        store.add_or_update("aa" * 32, "A")
        store.add_or_update("bb" * 32, "B")
        names = [c.name for c in store.contacts]
        assert "A" in names
        assert "B" in names

    def test_remove(self):
        from app.backends.spi_contact_store import SpiContactStore

        store = SpiContactStore()
        store.add_or_update("cc" * 32, "Charlie")
        assert len(store.contacts) == 1
        store.remove("cc" * 32)
        assert len(store.contacts) == 0
        assert store.get_by_name("Charlie") is None

    def test_get_by_public_key(self):
        from app.backends.spi_contact_store import SpiContactStore

        store = SpiContactStore()
        store.add_or_update("dd" * 32, "Dave")
        assert store.get_by_public_key("dd" * 32).name == "Dave"
        assert store.get_by_public_key("ee" * 32) is None


# ---------------------------------------------------------------------------
# Channel DB adapter
# ---------------------------------------------------------------------------


class TestSpiChannelDB:
    def test_get_channels_empty(self):
        from app.backends.spi_channel_db import SpiChannelDB

        db = SpiChannelDB()
        assert db.get_channels() == []

    def test_get_channels_after_manual_set(self):
        from app.backends.spi_channel_db import SpiChannelDB

        db = SpiChannelDB()
        db._cache = [{"name": "General", "secret": "AA" * 16}]
        channels = db.get_channels()
        assert len(channels) == 1
        assert channels[0]["name"] == "General"


# ---------------------------------------------------------------------------
# Config file integration
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG_YAML = """\
node:
  name: TestNode
radio:
  frequency: 910525000
  tx_power: 22
  bandwidth: 62500
  spreading_factor: 7
  coding_rate: 5
hardware:
  profile: waveshare
"""


class TestSpiConfigFile:
    def test_load_config_parses_yaml(self, tmp_path):
        from app.spi_config_file import load_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(_MINIMAL_CONFIG_YAML)
        cfg = load_config(cfg_path)
        assert cfg["hardware"]["profile"] == "waveshare"
        assert cfg["radio"]["frequency"] == 910525000
        assert cfg["node"]["name"] == "TestNode"

    def test_load_config_applies_defaults(self, tmp_path):
        from app.spi_config_file import load_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("hardware:\n  profile: waveshare\n")
        cfg = load_config(cfg_path)
        assert cfg["radio"]["preamble_length"] == 17
        assert cfg["radio"]["sync_word"] == 13380
        assert cfg["logging"]["level"] == "INFO"

    def test_load_config_missing_profile_raises(self, tmp_path):
        from app.spi_config_file import load_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("radio:\n  frequency: 910525000\n")
        with pytest.raises(RuntimeError, match="missing hardware.profile"):
            load_config(cfg_path)

    def test_load_config_file_not_found(self, tmp_path):
        from app.spi_config_file import load_config

        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_config_file_exists_detection(self, tmp_path):
        from app.spi_config_file import config_file_exists

        assert config_file_exists(tmp_path / "nope.yaml") is False
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(_MINIMAL_CONFIG_YAML)
        assert config_file_exists(cfg_path) is True

    def test_connection_type_spi_when_config_exists(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(_MINIMAL_CONFIG_YAML)

        monkeypatch.setenv("MESHCORE_CONFIG_FILE", str(cfg_path))
        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "")
        monkeypatch.setenv("MESHCORE_TCP_HOST", "")
        monkeypatch.setenv("MESHCORE_BLE_ADDRESS", "")

        from app.config import Settings

        s = Settings()
        assert s.connection_type == "spi"

    def test_config_file_plus_serial_rejected(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(_MINIMAL_CONFIG_YAML)

        monkeypatch.setenv("MESHCORE_CONFIG_FILE", str(cfg_path))
        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "/dev/ttyUSB0")
        monkeypatch.setenv("MESHCORE_TCP_HOST", "")
        monkeypatch.setenv("MESHCORE_BLE_ADDRESS", "")

        from app.config import Settings

        with pytest.raises(ValueError, match="Only one transport"):
            Settings()

    def test_no_config_file_defaults_to_serial(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHCORE_CONFIG_FILE", str(tmp_path / "nonexistent.yaml"))
        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "")
        monkeypatch.setenv("MESHCORE_TCP_HOST", "")
        monkeypatch.setenv("MESHCORE_BLE_ADDRESS", "")

        from app.config import Settings

        s = Settings()
        assert s.connection_type == "serial"

    def test_config_yaml_in_cwd_used_when_data_config_missing(self, tmp_path, monkeypatch):
        """config.yaml in current working directory is used when data/config.yaml does not exist."""
        (tmp_path / "config.yaml").write_text(_MINIMAL_CONFIG_YAML)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MESHCORE_CONFIG_FILE", raising=False)
        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "")
        monkeypatch.setenv("MESHCORE_TCP_HOST", "")
        monkeypatch.setenv("MESHCORE_BLE_ADDRESS", "")

        from app.config import Settings

        s = Settings()
        assert s.connection_type == "spi"
        assert s.spi_config_path is not None
        assert s.spi_config_path.resolve() == (tmp_path / "config.yaml").resolve()


# ---------------------------------------------------------------------------
# SpiBackend event bus
# ---------------------------------------------------------------------------


class TestEventBus:
    @pytest.fixture
    def bus(self):
        # Import from spi_backend to test the internal event bus
        from app.backends.spi_backend import _EventBus

        return _EventBus()

    async def test_subscribe_and_emit(self, bus):
        from meshcore import EventType

        received = []

        async def handler(event):
            received.append(event.payload)

        bus.subscribe(EventType.RX_LOG_DATA, handler)
        await bus.emit(EventType.RX_LOG_DATA, {"test": True})
        assert len(received) == 1
        assert received[0]["test"] is True

    async def test_unsubscribe(self, bus):
        from meshcore import EventType

        received = []

        async def handler(event):
            received.append(event)

        sub = bus.subscribe(EventType.RX_LOG_DATA, handler)
        sub.unsubscribe()
        await bus.emit(EventType.RX_LOG_DATA, {"test": True})
        assert len(received) == 0

    async def test_multiple_handlers(self, bus):
        from meshcore import EventType

        counts = {"a": 0, "b": 0}

        async def handler_a(event):
            counts["a"] += 1

        async def handler_b(event):
            counts["b"] += 1

        bus.subscribe(EventType.ACK, handler_a)
        bus.subscribe(EventType.ACK, handler_b)
        await bus.emit(EventType.ACK, {})
        assert counts["a"] == 1
        assert counts["b"] == 1


# ---------------------------------------------------------------------------
# SpiBackend method contracts (mocked — no real hardware)
# ---------------------------------------------------------------------------


class TestSpiBackendMethods:
    """Verify SpiBackend methods return meshcore-compatible Event objects."""

    @pytest.fixture
    def backend(self):
        from app.backends.spi_backend import SpiBackend

        be = SpiBackend()
        be._connected = True
        be._self_info = {
            "public_key": "ab" * 32,
            "adv_name": "TestNode",
            "name": "TestNode",
            "lat": 0.0,
            "lon": 0.0,
            "tx_power": 22,
        }
        be._radio = MagicMock()
        be._radio.get_last_rssi.return_value = -90
        be._radio.get_last_snr.return_value = 5.0
        be._node = MagicMock()
        be._identity = MagicMock()
        be._identity.get_public_key.return_value = bytes.fromhex("ab" * 32)
        return be

    async def test_disconnect(self, backend):
        backend._dispatcher_task = None
        backend._refresh_task = None
        await backend.disconnect()
        assert backend.is_connected is False

    async def test_self_info(self, backend):
        info = backend.self_info
        assert info is not None
        assert info["adv_name"] == "TestNode"

    async def test_get_msg_returns_no_more(self, backend):
        from meshcore import EventType

        result = await backend.get_msg()
        assert result.type == EventType.NO_MORE_MSGS

    async def test_set_time_is_noop(self, backend):
        from meshcore import EventType

        result = await backend.set_time(1234567890)
        assert result.type == EventType.OK

    async def test_set_name(self, backend):
        await backend.set_name("NewName")
        assert backend.self_info["adv_name"] == "NewName"

    async def test_set_coords(self, backend):
        await backend.set_coords(lat=51.5, lon=-0.1)
        assert backend.self_info["lat"] == 51.5
        assert backend.self_info["lon"] == -0.1

    async def test_set_tx_power(self, backend):
        await backend.set_tx_power(val=10)
        backend._radio.set_tx_power.assert_called_once_with(10)
        assert backend.self_info["tx_power"] == 10

    async def test_send_device_query(self, backend):
        from meshcore import EventType

        result = await backend.send_device_query()
        assert result.type == EventType.DEVICE_INFO
        assert result.payload["name"] == "TestNode"

    async def test_export_private_key(self, backend, tmp_path):
        seed = os.urandom(32)

        with patch("app.spi_identity.export_identity", return_value=seed):
            from meshcore import EventType

            result = await backend.export_private_key()
            assert result.type == EventType.PRIVATE_KEY
            assert result.payload["key"] == seed.hex()

    async def test_query_path_hash_mode(self, backend):
        mode, supported = await backend.query_path_hash_mode()
        assert mode == 0
        assert supported is False

    async def test_subscribe_returns_subscription(self, backend):
        from meshcore import EventType

        sub = backend.subscribe(EventType.RX_LOG_DATA, lambda e: None)
        assert hasattr(sub, "unsubscribe")
        sub.unsubscribe()  # Should not raise

    async def test_get_contact_by_key_prefix(self, backend):
        from app.backends.spi_contact_store import SpiContactStore

        store = SpiContactStore()
        store.add_or_update("aabb" * 8, "Alice")
        backend._contact_store = store

        result = backend.get_contact_by_key_prefix("aabb")
        assert result is not None
        assert result.name == "Alice"

        assert backend.get_contact_by_key_prefix("zzzz") is None


# ---------------------------------------------------------------------------
# Phase 3: Integration — packet pipeline with SPI backend (mock radio, real DB)
# ---------------------------------------------------------------------------


class TestSpiBackendPacketPipelineIntegration:
    """Integration tests: SpiBackend RX_LOG_DATA → event handlers → process_raw_packet → DB + broadcast."""

    @pytest.mark.asyncio
    async def test_spi_backend_rx_log_data_flows_to_packet_processor(
        self, test_db, captured_broadcasts
    ):
        """Emitting RX_LOG_DATA on SpiBackend reaches process_raw_packet; channel message is stored and broadcast."""
        if "channel_message" not in _PIPELINE_FIXTURES:
            pytest.skip("fixtures/websocket_events.json not found or missing channel_message")

        from meshcore import EventType

        from app.backends.spi_backend import SpiBackend
        from app.event_handlers import register_event_handlers
        from app.repository import ChannelRepository, MessageRepository

        fixture = _PIPELINE_FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(),
            name=fixture["channel_name"],
            is_hashtag=True,
        )

        backend = SpiBackend()
        backend._connected = True
        register_event_handlers(backend)

        broadcasts, mock_broadcast = captured_broadcasts
        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await backend._event_bus.emit(
                EventType.RX_LOG_DATA,
                {
                    "payload": fixture["raw_packet_hex"],
                    "snr": 7.5,
                    "rssi": -85,
                },
            )

        messages = await MessageRepository.get_all(
            msg_type="CHAN",
            conversation_key=fixture["channel_key_hex"].upper(),
            limit=10,
        )
        assert len(messages) == 1
        assert "Flightless" in messages[0].text or "hashtag" in messages[0].text

        message_broadcasts = [b for b in broadcasts if b["type"] == "message"]
        assert len(message_broadcasts) == 1
        assert (
            message_broadcasts[0]["data"]["conversation_key"] == fixture["channel_key_hex"].upper()
        )


# ---------------------------------------------------------------------------
# Phase 4.4: Fanout integration for SPI — ensure SPI-originated traffic drives fanout modules
# ---------------------------------------------------------------------------


class TestSpiBackendFanoutIntegration:
    """End-to-end: SpiBackend RX_LOG_DATA → packet_processor → websocket.broadcast_event → fanout."""

    @pytest.mark.asyncio
    async def test_spi_channel_message_triggers_fanout(self, test_db):
        """SPI channel message should result in fanout_manager.broadcast_message being called."""
        if "channel_message" not in _PIPELINE_FIXTURES:
            pytest.skip("fixtures/websocket_events.json not found or missing channel_message")

        from meshcore import EventType

        from app.backends.spi_backend import SpiBackend
        from app.event_handlers import register_event_handlers
        from app.repository import ChannelRepository

        fixture = _PIPELINE_FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(),
            name=fixture["channel_name"],
            is_hashtag=True,
        )

        backend = SpiBackend()
        backend._connected = True
        register_event_handlers(backend)

        with (
            patch("app.fanout.manager.fanout_manager.broadcast_message", new_callable=AsyncMock)
            as mock_broadcast_message,
            patch("app.fanout.manager.fanout_manager.broadcast_raw", new_callable=AsyncMock)
            as mock_broadcast_raw,
        ):
            await backend._event_bus.emit(
                EventType.RX_LOG_DATA,
                {
                    "payload": fixture["raw_packet_hex"],
                    "snr": 7.5,
                    "rssi": -85,
                },
            )

            # Allow broadcast_event's background tasks to run
            await asyncio.sleep(0)

        # Fanout should receive both the decoded message and the raw packet broadcast
        mock_broadcast_message.assert_called_once()
        mock_broadcast_raw.assert_called_once()

        message_arg = mock_broadcast_message.call_args.args[0]
        assert message_arg["type"] == "CHAN"
        assert message_arg["conversation_key"] == fixture["channel_key_hex"].upper()
        assert message_arg.get("channel_name") == fixture["channel_name"]
