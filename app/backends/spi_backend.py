"""SpiBackend — wraps ``pymc_core`` for direct SPI communication with LoRa hardware.

This backend turns a Raspberry Pi + LoRa HAT into a self-contained MeshCore
node.  Instead of talking to an external radio over serial/TCP/BLE, it drives
the SX1262 chip directly over the SPI bus using ``pymc_core``.

Key design decisions:
  - pymc_core's Dispatcher ``run_forever()`` runs as an asyncio background task.
  - Raw packets received by the Dispatcher are forwarded to RemoteTerm's
    ``process_raw_packet()`` via the ``raw_packet_callback``, exactly like
    the meshcore library's ``RX_LOG_DATA`` event.
  - Contact and channel data live in RemoteTerm's database.  Thin adapter
    objects (:class:`SpiContactStore` and :class:`SpiChannelDB`) present
    them in the shape pymc_core expects.
  - meshcore ``EventType`` enums are synthesised so that RemoteTerm's
    existing event handlers continue to work unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.radio_backend import RadioBackend

if TYPE_CHECKING:
    from meshcore import EventType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight event bridge
# ---------------------------------------------------------------------------


class _Subscription:
    """Mimics meshcore's Subscription object."""

    def __init__(self, bus: _EventBus, event_type: Any, handler: Any) -> None:
        self._bus = bus
        self._event_type = event_type
        self._handler = handler

    def unsubscribe(self) -> None:
        self._bus.remove(self._event_type, self._handler)


class _Event:
    """Mimics meshcore's Event object with .type and .payload."""

    __slots__ = ("type", "payload")

    def __init__(self, event_type: Any, payload: dict[str, Any]) -> None:
        self.type = event_type
        self.payload = payload


class _EventBus:
    """Simple publish/subscribe bus that emits meshcore-compatible Event objects."""

    def __init__(self) -> None:
        self._handlers: dict[Any, list[Any]] = {}

    def subscribe(self, event_type: Any, handler: Any) -> _Subscription:
        self._handlers.setdefault(event_type, []).append(handler)
        return _Subscription(self, event_type, handler)

    def remove(self, event_type: Any, handler: Any) -> None:
        lst = self._handlers.get(event_type, [])
        try:
            lst.remove(handler)
        except ValueError:
            pass

    async def emit(self, event_type: Any, payload: dict[str, Any]) -> None:
        event = _Event(event_type, payload)
        for handler in list(self._handlers.get(event_type, [])):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Error in event handler for %s", event_type)

    def emit_nowait(self, event_type: Any, payload: dict[str, Any]) -> None:
        """Schedule emission on the running event loop (fire-and-forget)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(event_type, payload))
        except RuntimeError:
            logger.warning("No running loop; dropping %s event", event_type)


class SpiBackend(RadioBackend):
    """RadioBackend implementation using pymc_core for direct SPI radio access."""

    def __init__(self) -> None:
        self._node: Any = None  # pymc_core.MeshNode
        self._radio: Any = None  # SX1262Radio
        self._identity: Any = None  # pymc_core.LocalIdentity
        self._dispatcher_task: asyncio.Task | None = None
        self._connected = False
        self._self_info: dict[str, Any] | None = None
        self._event_bus = _EventBus()

        # Adapters injected at connect() time
        self._contact_store: Any = None
        self._channel_db: Any = None

        # Periodic cache refresh task
        self._refresh_task: asyncio.Task | None = None

        # Slot -> channel name for send_chan_msg. App calls set_channel(slot, name, secret)
        # before send; we must send to that channel, not channels[slot] from DB (DB order
        # is ORDER BY name, so slot 0 would be #bot not the channel we loaded).
        self._channel_slot_map: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Initialisation (called by RadioManager._connect_spi)
    # ------------------------------------------------------------------

    async def initialise(
        self,
        *,
        profile_name: str,
        identity_seed: bytes,
        node_name: str,
        frequency: int,
        bandwidth: int,
        spreading_factor: int,
        coding_rate: int,
        tx_power: int,
        preamble_length: int,
        sync_word: int,
        bus_override: int | None = None,
        cs_override: int | None = None,
        reset_override: int | None = None,
        busy_override: int | None = None,
        irq_override: int | None = None,
    ) -> None:
        """Set up the SPI radio and start the Dispatcher.

        Raises ``RuntimeError`` on hardware failure (e.g. SPI not available).
        """
        from pymc_core import LocalIdentity, MeshNode
        from pymc_core.hardware.sx1262_wrapper import SX1262Radio

        from app.backends.spi_channel_db import SpiChannelDB
        from app.backends.spi_config import get_profile
        from app.backends.spi_contact_store import SpiContactStore

        profile = get_profile(profile_name)

        radio_kwargs: dict[str, Any] = {
            "bus_id": bus_override if bus_override is not None else profile.bus_id,
            "cs_id": profile.cs_id,
            "cs_pin": cs_override if cs_override is not None else profile.cs_pin,
            "reset_pin": reset_override if reset_override is not None else profile.reset_pin,
            "busy_pin": busy_override if busy_override is not None else profile.busy_pin,
            "irq_pin": irq_override if irq_override is not None else profile.irq_pin,
            "txen_pin": profile.txen_pin,
            "rxen_pin": profile.rxen_pin,
            "frequency": frequency,
            "tx_power": tx_power,
            "spreading_factor": spreading_factor,
            "bandwidth": bandwidth,
            "coding_rate": coding_rate,
            "preamble_length": preamble_length,
            "sync_word": sync_word,
            "is_waveshare": profile.is_waveshare,
            "use_dio3_tcxo": profile.use_dio3_tcxo,
            "use_dio2_rf": profile.use_dio2_rf,
        }

        logger.info("Initialising SX1262 radio with profile %r", profile_name)
        self._radio = SX1262Radio(**radio_kwargs)
        self._radio.begin()

        self._identity = LocalIdentity(seed=identity_seed)
        pub_hex = self._identity.get_public_key().hex()

        config = {
            "node": {"name": node_name},
            "radio": {
                "frequency": frequency,
                "bandwidth": bandwidth,
                "spreading_factor": spreading_factor,
                "coding_rate": coding_rate,
                "tx_power": tx_power,
                "preamble_length": preamble_length,
            },
        }

        self._contact_store = SpiContactStore()
        self._channel_db = SpiChannelDB()
        await self._contact_store.refresh()
        await self._channel_db.refresh()

        self._node = MeshNode(
            radio=self._radio,
            local_identity=self._identity,
            config=config,
            contacts=self._contact_store,
            channel_db=self._channel_db,
        )

        # Hook raw packet callback to feed RemoteTerm's packet processor
        self._node.dispatcher.set_raw_packet_callback(self._on_raw_packet)

        # Start the Dispatcher maintenance loop as a background task
        self._dispatcher_task = asyncio.create_task(self._node.start(), name="spi-dispatcher")

        # Start periodic cache refresh (every 60s)
        self._refresh_task = asyncio.create_task(self._periodic_refresh(), name="spi-cache-refresh")

        self._self_info = {
            "public_key": pub_hex,
            "adv_name": node_name,
            "name": node_name,
            "lat": 0.0,
            "lon": 0.0,
            "tx_power": tx_power,
            "radio_freq": frequency / 1_000_000,
            "radio_bw": bandwidth / 1_000,
            "radio_sf": spreading_factor,
            "radio_cr": coding_rate,
        }
        self._connected = True
        logger.info("SPI backend online — node %s (%s…)", node_name, pub_hex[:12])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _on_raw_packet(self, pkt: Any, raw_data: bytes, *_extra: Any) -> None:
        """Bridge raw packets into RemoteTerm's event system.

        Emits an ``RX_LOG_DATA``-shaped event so that the existing
        ``on_rx_log_data`` handler and ``process_raw_packet`` pipeline work
        unchanged.
        """
        from meshcore import EventType

        rssi = getattr(pkt, "_rssi", None) or (self._radio.get_last_rssi() if self._radio else None)
        snr = getattr(pkt, "_snr", None) or (self._radio.get_last_snr() if self._radio else None)

        await self._event_bus.emit(
            EventType.RX_LOG_DATA,
            {
                "payload": raw_data.hex(),
                "rssi": rssi,
                "snr": snr,
            },
        )

    async def _periodic_refresh(self) -> None:
        """Refresh contact/channel caches from the DB every 60 seconds."""
        while True:
            await asyncio.sleep(60)
            try:
                if self._contact_store:
                    await self._contact_store.refresh()
                if self._channel_db:
                    await self._channel_db.refresh()
            except Exception:
                logger.exception("Error refreshing SPI caches")

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        self._connected = False
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
        if self._node:
            self._node.stop()
        if self._radio:
            try:
                if hasattr(self._radio, "cleanup"):
                    # cleanup() calls lora.end() + gpio_manager.cleanup_all(),
                    # which closes GPIO fds so the reconnect loop can reopen them.
                    self._radio.cleanup()
                elif hasattr(self._radio, "sleep"):
                    self._radio.sleep()
            except Exception:
                pass
        self._node = None
        self._radio = None
        self._identity = None
        logger.info("SPI backend disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def self_info(self) -> dict[str, Any] | None:
        return self._self_info

    async def start_auto_message_fetching(self) -> None:
        pass  # Dispatcher handles RX via callbacks

    async def stop_auto_message_fetching(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def get_contacts(self) -> Any:
        """Return contacts from the DB as a meshcore-shaped result."""
        from meshcore import EventType

        from app.repository import ContactRepository

        # meshcore.EventType may use CONTACTS or CONTACT_LIST depending on version
        contact_list_type = getattr(EventType, "CONTACT_LIST", None) or getattr(
            EventType, "CONTACTS", None
        )
        if contact_list_type is None:
            contact_list_type = object()  # sentinel so sync accepts result (not ERROR)

        db_contacts = await ContactRepository.get_all()
        payload: dict[str, dict[str, Any]] = {}
        for c in db_contacts:
            payload[c.public_key.lower()] = {
                "public_key": c.public_key.lower(),
                "adv_name": c.name,
                "type": c.type,
                "flags": c.flags,
            }
        return _Event(contact_list_type, payload)

    async def add_contact(self, contact_dict: dict[str, Any]) -> Any:
        from meshcore import EventType

        from app.repository import ContactRepository

        pk = contact_dict.get("public_key", "").lower()
        name = contact_dict.get("adv_name", "") or contact_dict.get("name", "")
        await ContactRepository.upsert(
            {
                "public_key": pk,
                "name": name,
                "type": contact_dict.get("type", 0),
                "flags": contact_dict.get("flags", 0),
                "last_seen": int(time.time()),
            }
        )
        if self._contact_store:
            self._contact_store.add_or_update(pk, name)
        # Return OK so radio_sync (and callers checking result.type == EventType.OK) sees success.
        # meshcore may not define CONTACT_ADDED; the library uses OK for add_contact success.
        return _Event(EventType.OK, {"public_key": pk})

    async def remove_contact(self, contact_data: Any) -> Any:
        from meshcore import EventType

        pk = getattr(contact_data, "public_key", str(contact_data)).lower()
        from app.repository import ContactRepository

        await ContactRepository.delete(pk)
        if self._contact_store:
            self._contact_store.remove(pk)
        # Return OK so radio_sync (and any caller checking result.type == EventType.OK) sees success.
        # meshcore does not define CONTACT_REMOVED; the library uses OK for remove_contact success.
        return _Event(EventType.OK, {"public_key": pk})

    def get_contact_by_key_prefix(self, prefix: str) -> Any:
        if not self._contact_store:
            return None
        prefix_lower = prefix.lower()
        for c in self._contact_store.contacts:
            if c.public_key.startswith(prefix_lower):
                return c
        return None

    def evict_contact_from_cache(self, public_key: str) -> None:
        if self._contact_store:
            self._contact_store.remove(public_key)

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def get_channel(self, idx: int) -> Any:
        from meshcore import EventType

        from app.repository import ChannelRepository

        channels = await ChannelRepository.get_all()
        if 0 <= idx < len(channels):
            ch = channels[idx]
            return _Event(
                EventType.CHANNEL_INFO,
                {
                    "idx": idx,
                    "name": ch.name,
                    "key": ch.key,
                },
            )
        return _Event(EventType.ERROR, {"error": f"Channel index {idx} out of range"})

    async def set_channel(
        self,
        *,
        channel_idx: int,
        channel_name: str,
        channel_secret: bytes,
    ) -> Any:
        from meshcore import EventType

        from app.repository import ChannelRepository

        key_hex = channel_secret.hex().upper()
        await ChannelRepository.upsert(key_hex, channel_name)
        if self._channel_db:
            await self._channel_db.refresh()
        self._channel_slot_map[channel_idx] = channel_name
        return _Event(
            EventType.CHANNEL_INFO,
            {
                "idx": channel_idx,
                "name": channel_name,
                "key": key_hex,
            },
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_msg(
        self,
        dst: Any,
        msg: str,
        *,
        timestamp: int | None = None,
    ) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        contact_name = getattr(dst, "name", None) or str(dst)
        result = await self._node.send_text(contact_name, msg)

        ack_crc = result.get("crc", 0)
        # meshcore uses MSG_SENT for successful send acknowledgements; mirror that here
        return _Event(
            EventType.MSG_SENT,
            {
                "expected_ack": f"{ack_crc:08x}" if isinstance(ack_crc, int) else str(ack_crc),
                "suggested_timeout": 15000,
                "success": result.get("success", False),
            },
        )

    async def send_cmd(self, dst: Any, cmd: str) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        contact_name = getattr(dst, "name", None)
        if not contact_name and self._contact_store:
            pk = str(dst).lower()
            c = self._contact_store.get_by_public_key(pk)
            contact_name = c.name if c else None
        if not contact_name:
            return _Event(EventType.ERROR, {"error": "Contact not found"})

        result = await self._node.send_repeater_command(contact_name, cmd)
        # meshcore's EventType enum does not define CMD_RESPONSE in all versions.
        # For SPI mode we already have the full response dict from pymc_core, so
        # return OK and let the router decide whether it needs to wait for a
        # follow-up TXT_MSG event.
        return _Event(EventType.OK, result)

    async def send_chan_msg(
        self,
        *,
        chan: int,
        msg: str,
        timestamp: int | None = None,
    ) -> Any:
        from meshcore import EventType

        if not self._node or not self._channel_db:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        # Use the channel loaded into this slot by set_channel, not DB list order
        # (get_channels() is ORDER BY name, so slot 0 would be #bot etc.).
        group_name = self._channel_slot_map.get(chan)
        if not group_name:
            channels = self._channel_db.get_channels()
            if 0 <= chan < len(channels):
                group_name = channels[chan]["name"]
        if group_name:
            result = await self._node.send_group_text(group_name, msg)
            return _Event(
                EventType.MSG_SENT,
                {
                    "success": result.get("success", False),
                },
            )
        return _Event(EventType.ERROR, {"error": f"Channel index {chan} out of range"})

    async def get_msg(self, timeout: float = 2.0) -> Any:
        from meshcore import EventType

        # SPI backend receives messages via callbacks, not polling
        return _Event(EventType.NO_MORE_MSGS, {})

    # ------------------------------------------------------------------
    # Radio configuration
    # ------------------------------------------------------------------

    async def send_advert(self, *, flood: bool) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        from pymc_core.protocol import PacketBuilder

        pkt = PacketBuilder.create_advert(
            local_identity=self._identity,
            name=self._self_info.get("adv_name", "RemoteTerm") if self._self_info else "RemoteTerm",
            lat=self._self_info.get("lat", 0.0) if self._self_info else 0.0,
            lon=self._self_info.get("lon", 0.0) if self._self_info else 0.0,
        )
        await self._node.dispatcher.send_packet(pkt, wait_for_ack=False)
        # Return OK so radio_sync (and callers checking result.type == EventType.OK) sees success.
        # meshcore may not define ADVERT_SENT; the library uses OK for send_advert success.
        return _Event(EventType.OK, {"flood": flood})

    async def set_time(self, unix_ts: int) -> Any:
        from meshcore import EventType

        # We ARE the clock in SPI mode
        return _Event(EventType.OK, {})

    async def send_device_query(self) -> Any:
        from meshcore import EventType

        return _Event(
            EventType.DEVICE_INFO,
            {
                "firmware_ver": "pymc_core",
                "path_hash_mode": 0,
                "name": self._self_info.get("adv_name", "") if self._self_info else "",
                "public_key": self._self_info.get("public_key", "") if self._self_info else "",
                "tx_power": self._self_info.get("tx_power", 22) if self._self_info else 22,
            },
        )

    async def set_name(self, name: str) -> Any:
        from meshcore import EventType

        if self._self_info:
            self._self_info["adv_name"] = name
            self._self_info["name"] = name
        if self._node:
            self._node.node_name = name
        return _Event(EventType.OK, {})

    async def set_coords(self, *, lat: float, lon: float) -> Any:
        from meshcore import EventType

        if self._self_info:
            self._self_info["lat"] = lat
            self._self_info["lon"] = lon
        return _Event(EventType.OK, {})

    async def set_tx_power(self, *, val: int) -> Any:
        from meshcore import EventType

        if self._radio and hasattr(self._radio, "set_tx_power"):
            self._radio.set_tx_power(val)
        if self._self_info:
            self._self_info["tx_power"] = val
        return _Event(EventType.OK, {})

    async def set_radio(
        self,
        *,
        freq: float,
        bw: float,
        sf: int,
        cr: int,
    ) -> Any:
        from meshcore import EventType

        if self._radio:
            if hasattr(self._radio, "set_frequency"):
                self._radio.set_frequency(int(freq))
            if hasattr(self._radio, "set_bandwidth"):
                self._radio.set_bandwidth(int(bw))
            if hasattr(self._radio, "set_spreading_factor"):
                self._radio.set_spreading_factor(sf)
        return _Event(EventType.OK, {})

    async def set_flood_scope(self, scope: str) -> Any:
        from meshcore import EventType

        return _Event(EventType.OK, {})

    async def set_path_hash_mode(self, mode: int) -> Any:
        from meshcore import EventType

        return _Event(EventType.OK, {})

    async def send_appstart(self) -> Any:
        from meshcore import EventType

        return _Event(EventType.SELF_INFO, self._self_info or {})

    async def reboot(self) -> None:
        logger.info("Rebooting SPI radio…")
        await self.disconnect()
        # Give the driver time to release GPIO (IRQ pin 16, etc.) before the
        # connection monitor reconnects and creates a new SX1262Radio instance.
        # 5s is conservative; 2s was insufficient on some Pi/hardware.
        await asyncio.sleep(5)
        # Caller (RadioManager) is expected to re-connect after this

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    async def export_private_key(self) -> Any:
        from meshcore import EventType

        from app.spi_identity import export_identity

        seed = export_identity()
        if seed:
            return _Event(EventType.PRIVATE_KEY, {"key": seed.hex()})
        return _Event(EventType.ERROR, {"error": "No identity configured"})

    async def import_private_key(self, key: bytes) -> Any:
        from meshcore import EventType

        from app.spi_identity import import_identity

        import_identity(key)
        return _Event(EventType.OK, {})

    # ------------------------------------------------------------------
    # Repeater operations
    # ------------------------------------------------------------------

    async def send_login(self, public_key: str, password: str) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        contact_name = self._resolve_name(public_key)
        if not contact_name:
            return _Event(EventType.ERROR, {"error": "Contact not found"})
        result = await self._node.send_login(contact_name, password)
        return _Event(EventType.LOGIN_SUCCESS, result)

    async def req_status_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})
        contact_name = self._resolve_name(public_key)
        if not contact_name:
            return _Event(EventType.ERROR, {"error": "Contact not found"})
        # Prefer a binary REQ protocol request here. If we rely on higher-level
        # helpers that may be implemented as text/CLI on some pymc_core
        # versions/firmware builds, the repeater may respond with:
        #   TXT_MSG "Unknown command"
        # which then breaks the API contract for this endpoint.
        protocol_code = 0x01  # REQ_TYPE_GET_STATUS (MeshCore)
        try:
            from pymc_core.node.handlers.protocol_request import (
                REQ_TYPE_GET_STATUS as _REQ_TYPE_GET_STATUS,
            )

            protocol_code = int(_REQ_TYPE_GET_STATUS)
        except Exception:
            # Fall back to the MeshCore-known constant.
            pass

        raw_stats: dict[str, Any] | None = None
        try:
            proto_result = await self._node.send_protocol_request(contact_name, protocol_code, b"")
            if isinstance(proto_result, dict) and proto_result.get("success") is True:
                # Different pymc_core versions may use slightly different field
                # names for the parsed payload.
                parsed_data = (
                    proto_result.get("parsed_data")
                    or proto_result.get("parsed")
                    or proto_result.get("parsed_response")
                    or {}
                )

                # Expected: raw stats dict directly.
                if isinstance(parsed_data, dict) and any(
                    k in parsed_data
                    for k in (
                        "batt_milli_volts",
                        "curr_tx_queue_len",
                        "noise_floor",
                        "last_rssi",
                        "n_packets_recv",
                        "n_packets_sent",
                    )
                ):
                    raw_stats = parsed_data

                # Some wrappers may nest the raw stats under a "raw" key.
                if raw_stats is None and isinstance(parsed_data, dict):
                    nested_raw = parsed_data.get("raw")
                    if isinstance(nested_raw, dict) and any(
                        k in nested_raw
                        for k in (
                            "batt_milli_volts",
                            "curr_tx_queue_len",
                            "noise_floor",
                            "last_rssi",
                            "n_packets_recv",
                            "n_packets_sent",
                        )
                    ):
                        raw_stats = nested_raw
        except Exception:
            logger.exception("SPI binary status request failed; falling back")

        if raw_stats is not None:
            mapped = {
                "success": True,
                "repeater": contact_name,
                "bat": raw_stats.get("batt_milli_volts", 0),
                "tx_queue_len": raw_stats.get("curr_tx_queue_len", 0),
                "noise_floor": raw_stats.get("noise_floor", 0),
                "last_rssi": raw_stats.get("last_rssi", 0),
                "last_snr": float(raw_stats.get("last_snr", 0.0)),
                "nb_recv": raw_stats.get("n_packets_recv", 0),
                "nb_sent": raw_stats.get("n_packets_sent", 0),
                "airtime": raw_stats.get("total_air_time_secs", 0),
                "rx_airtime": raw_stats.get("total_rx_air_time_secs", 0),
                "uptime": raw_stats.get("total_up_time_secs", 0),
                "sent_flood": raw_stats.get("n_sent_flood", 0),
                "sent_direct": raw_stats.get("n_sent_direct", 0),
                "recv_flood": raw_stats.get("n_recv_flood", 0),
                "recv_direct": raw_stats.get("n_recv_direct", 0),
                "flood_dups": raw_stats.get("n_flood_dups", 0),
                "direct_dups": raw_stats.get("n_direct_dups", 0),
                "full_evts": raw_stats.get("err_events", 0),
            }
            return _Event(EventType.STATUS_RESPONSE, mapped)

        # Last-resort fallback: use the helper on the pymc_core node object.
        result = await self._node.send_status_request(contact_name)
        return _Event(EventType.STATUS_RESPONSE, result)

    async def req_telemetry_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})
        contact_name = self._resolve_name(public_key)
        if not contact_name:
            return _Event(EventType.ERROR, {"error": "Contact not found"})
        result = await self._node.send_telemetry_request(contact_name, timeout=timeout)
        return _Event(EventType.TELEMETRY_RESPONSE, result)

    async def fetch_all_neighbours(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        """Fetch neighbours via repeater CLI 'neighbors' command.

        The repeater firmware responds with lines in the form:
            {PUBKEY_PREFIX}:{secs_ago}:{snr_times_4}
        """
        if not self._node:
            return {"error": "Not connected"}

        contact_name = self._resolve_name(public_key)
        if not contact_name:
            return {"error": "Contact not found"}

        try:
            result = await self._node.send_repeater_command(contact_name, "neighbors")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("SPI neighbours command failed")
            return {"error": str(exc)}

        text = ""
        if isinstance(result, dict):
            text = str(result.get("response") or result.get("text") or "")
        else:
            text = str(result)

        neighbours: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            if len(parts) != 3:
                continue
            pubkey_prefix, secs_str, snr4_str = parts
            try:
                secs_ago = int(secs_str)
                snr_db = int(snr4_str) / 4.0
            except ValueError:
                continue
            neighbours.append(
                {
                    "pubkey": pubkey_prefix,
                    "secs_ago": secs_ago,
                    "snr": snr_db,
                }
            )

        return {"neighbours": neighbours}

    async def req_acl_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        from meshcore import EventType

        return _Event(EventType.ERROR, {"error": "Not yet implemented for SPI backend"})

    async def send_trace(self, *, path: str, tag: int) -> Any:
        from meshcore import EventType

        if not self._node:
            return _Event(EventType.ERROR, {"error": "Not connected"})

        # path is a hex string of contact public key; resolve to name
        contact_name = self._resolve_name(path)
        if not contact_name:
            return _Event(EventType.ERROR, {"error": "Contact not found for trace"})

        result = await self._node.send_trace_packet(contact_name, tag, auth_code=0)
        return _Event(EventType.TRACE_DATA, result)

    async def wait_for_event(
        self,
        event_type: EventType,
        *,
        attribute_filters: dict[str, Any] | None = None,
        timeout: float = 15,
    ) -> Any:
        """Wait for a specific event on the internal bus."""
        result: list[_Event] = []
        captured = asyncio.Event()

        def _handler(event: _Event) -> None:
            if attribute_filters:
                for key, val in attribute_filters.items():
                    if event.payload.get(key) != val:
                        return
            result.append(event)
            captured.set()

        sub = self._event_bus.subscribe(event_type, _handler)
        try:
            await asyncio.wait_for(captured.wait(), timeout=timeout)
            return result[0] if result else None
        except asyncio.TimeoutError:
            return None
        finally:
            sub.unsubscribe()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def get_stats_core(self) -> Any:
        from meshcore import EventType

        return _Event(
            EventType.STATS_CORE,
            {
                "uptime": int(time.time()),
                "backend": "spi",
            },
        )

    async def get_stats_radio(self) -> Any:
        from meshcore import EventType

        stats: dict[str, Any] = {"backend": "spi"}
        if self._radio:
            stats["rssi"] = self._radio.get_last_rssi()
            stats["snr"] = self._radio.get_last_snr()
        if self._node and hasattr(self._node.dispatcher, "get_filter_stats"):
            stats["filter"] = self._node.dispatcher.get_filter_stats()
        return _Event(EventType.STATS_RADIO, stats)

    # ------------------------------------------------------------------
    # Events / subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, event_type: Any, handler: Any) -> Any:
        return self._event_bus.subscribe(event_type, handler)

    # ------------------------------------------------------------------
    # Backend-specific helpers
    # ------------------------------------------------------------------

    async def query_path_hash_mode(self) -> tuple[int, bool]:
        # SPI backend doesn't use firmware path hash mode negotiation
        return (0, False)

    def _resolve_name(self, public_key: str) -> str | None:
        """Resolve a public key (or prefix) to a contact name."""
        if not self._contact_store:
            return None
        pk = public_key.lower()
        c = self._contact_store.get_by_public_key(pk)
        if c:
            return c.name or None
        # Try prefix match
        for contact in self._contact_store.contacts:
            if contact.public_key.startswith(pk):
                return contact.name or None
        return None
