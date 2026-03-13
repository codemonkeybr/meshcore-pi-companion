"""ClientBackend — wraps the ``meshcore`` library for serial/TCP/BLE radios.

Every method is a thin passthrough that delegates to the underlying
``MeshCore`` instance.  This is the default backend used when connecting
to an external radio running C++ MeshCore firmware.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.radio_backend import RadioBackend

if TYPE_CHECKING:
    from meshcore import MeshCore

logger = logging.getLogger(__name__)


class ClientBackend(RadioBackend):
    """RadioBackend implementation wrapping the meshcore client library."""

    def __init__(self, mc: MeshCore) -> None:
        self._mc = mc

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        await self._mc.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._mc.is_connected

    @property
    def self_info(self) -> dict[str, Any] | None:
        return self._mc.self_info

    async def start_auto_message_fetching(self) -> None:
        await self._mc.start_auto_message_fetching()

    async def stop_auto_message_fetching(self) -> None:
        await self._mc.stop_auto_message_fetching()

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def get_contacts(self) -> Any:
        return await self._mc.commands.get_contacts()

    async def add_contact(self, contact_dict: dict[str, Any]) -> Any:
        return await self._mc.commands.add_contact(contact_dict)

    async def remove_contact(self, contact_data: Any) -> Any:
        return await self._mc.commands.remove_contact(contact_data)

    def get_contact_by_key_prefix(self, prefix: str) -> Any:
        return self._mc.get_contact_by_key_prefix(prefix)

    def evict_contact_from_cache(self, public_key: str) -> None:
        self._mc._contacts.pop(public_key, None)

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def get_channel(self, idx: int) -> Any:
        return await self._mc.commands.get_channel(idx)

    async def set_channel(
        self,
        *,
        channel_idx: int,
        channel_name: str,
        channel_secret: bytes,
    ) -> Any:
        return await self._mc.commands.set_channel(
            channel_idx=channel_idx,
            channel_name=channel_name,
            channel_secret=channel_secret,
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
        kwargs: dict[str, Any] = {"dst": dst, "msg": msg}
        if timestamp is not None:
            kwargs["timestamp"] = timestamp
        return await self._mc.commands.send_msg(**kwargs)

    async def send_cmd(self, dst: Any, cmd: str) -> Any:
        return await self._mc.commands.send_cmd(dst, cmd)

    async def send_chan_msg(
        self,
        *,
        chan: int,
        msg: str,
        timestamp: int | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"chan": chan, "msg": msg}
        if timestamp is not None:
            kwargs["timestamp"] = timestamp
        return await self._mc.commands.send_chan_msg(**kwargs)

    async def get_msg(self, timeout: float = 2.0) -> Any:
        return await self._mc.commands.get_msg(timeout=timeout)

    # ------------------------------------------------------------------
    # Radio configuration
    # ------------------------------------------------------------------

    async def send_advert(self, *, flood: bool) -> Any:
        return await self._mc.commands.send_advert(flood=flood)

    async def set_time(self, unix_ts: int) -> Any:
        return await self._mc.commands.set_time(unix_ts)

    async def send_device_query(self) -> Any:
        return await self._mc.commands.send_device_query()

    async def set_name(self, name: str) -> Any:
        return await self._mc.commands.set_name(name)

    async def set_coords(self, *, lat: float, lon: float) -> Any:
        return await self._mc.commands.set_coords(lat=lat, lon=lon)

    async def set_tx_power(self, *, val: int) -> Any:
        return await self._mc.commands.set_tx_power(val=val)

    async def set_radio(
        self,
        *,
        freq: float,
        bw: float,
        sf: int,
        cr: int,
    ) -> Any:
        return await self._mc.commands.set_radio(freq=freq, bw=bw, sf=sf, cr=cr)

    async def set_flood_scope(self, scope: str) -> Any:
        return await self._mc.commands.set_flood_scope(scope)

    async def set_path_hash_mode(self, mode: int) -> Any:
        return await self._mc.commands.set_path_hash_mode(mode)

    async def send_appstart(self) -> Any:
        return await self._mc.commands.send_appstart()

    async def reboot(self) -> None:
        await self._mc.commands.reboot()

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    async def export_private_key(self) -> Any:
        return await self._mc.commands.export_private_key()

    async def import_private_key(self, key: bytes) -> Any:
        return await self._mc.commands.import_private_key(key)

    # ------------------------------------------------------------------
    # Repeater operations
    # ------------------------------------------------------------------

    async def send_login(self, public_key: str, password: str) -> Any:
        return await self._mc.commands.send_login(public_key, password)

    async def req_status_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        return await self._mc.commands.req_status_sync(
            public_key, timeout=timeout, min_timeout=min_timeout
        )

    async def req_telemetry_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        return await self._mc.commands.req_telemetry_sync(
            public_key, timeout=timeout, min_timeout=min_timeout
        )

    async def fetch_all_neighbours(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        return await self._mc.commands.fetch_all_neighbours(
            public_key, timeout=timeout, min_timeout=min_timeout
        )

    async def req_acl_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        return await self._mc.commands.req_acl_sync(
            public_key, timeout=timeout, min_timeout=min_timeout
        )

    async def send_trace(self, *, path: str, tag: int) -> Any:
        return await self._mc.commands.send_trace(path=path, tag=tag)

    async def wait_for_event(
        self,
        event_type: Any,
        *,
        attribute_filters: dict[str, Any] | None = None,
        timeout: float = 15,
    ) -> Any:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if attribute_filters is not None:
            kwargs["attribute_filters"] = attribute_filters
        return await self._mc.wait_for_event(event_type, **kwargs)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def get_stats_core(self) -> Any:
        return await self._mc.commands.get_stats_core()

    async def get_stats_radio(self) -> Any:
        return await self._mc.commands.get_stats_radio()

    # ------------------------------------------------------------------
    # Events / subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, event_type: Any, handler: Any) -> Any:
        return self._mc.subscribe(event_type, handler)

    # ------------------------------------------------------------------
    # Backend-specific helpers
    # ------------------------------------------------------------------

    async def query_path_hash_mode(self) -> tuple[int, bool]:
        """Detect path hash mode by querying the radio firmware.

        Uses a reader monkey-patch to capture the raw DEVICE_INFO frame as
        a fallback when the parsed payload is missing ``path_hash_mode``
        (e.g. due to stale .pyc files on WSL2 Windows mounts).
        """
        reader = self._mc._reader
        _original_handle_rx = reader.handle_rx
        _captured_frame: list[bytes] = []

        async def _capture_handle_rx(data: bytearray) -> None:
            from meshcore.packets import PacketType

            if len(data) > 0 and data[0] == PacketType.DEVICE_INFO.value:
                _captured_frame.append(bytes(data))
            return await _original_handle_rx(data)

        reader.handle_rx = _capture_handle_rx
        mode = 0
        supported = False
        try:
            device_query = await self._mc.commands.send_device_query()
            if device_query and "path_hash_mode" in device_query.payload:
                mode = device_query.payload["path_hash_mode"]
                supported = True
            elif _captured_frame:
                raw = _captured_frame[-1]
                fw_ver = raw[1] if len(raw) > 1 else 0
                if fw_ver >= 10 and len(raw) >= 82:
                    mode = raw[81]
                    supported = True
                    logger.warning(
                        "path_hash_mode=%d extracted from raw frame (stale .pyc? try: rm %s)",
                        mode,
                        getattr(
                            __import__("meshcore.reader", fromlist=["reader"]),
                            "__cached__",
                            "meshcore __pycache__/reader.*.pyc",
                        ),
                    )
            if supported:
                logger.info("Path hash mode: %d (supported)", mode)
            else:
                logger.debug("Firmware does not report path_hash_mode")
        except Exception as exc:
            logger.debug("Failed to query path_hash_mode: %s", exc)
        finally:
            reader.handle_rx = _original_handle_rx

        return mode, supported
