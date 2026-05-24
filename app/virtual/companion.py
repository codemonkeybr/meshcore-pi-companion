"""Virtual TCP companion for RemoteTerm.

Wraps pymc_core's CompanionBridge + CompanionFrameServer to expose the
MeshCore binary frame protocol over TCP. Persistence is handled via
the RemoteTerm SQLite database (companion_prefs table).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import socket
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.virtual.models import CompanionConfig, CompanionStatus

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class _RemoteTermCompanionFrameServer:
    """CompanionFrameServer subclass with aiosqlite persistence.

    Subclasses pymc_core's base frame server and overrides persistence hooks
    to store companion state (contacts, channels, queued messages) in the
    companion_prefs table as a JSON blob — identical pattern to
    pyMC_Repeater's RepeaterCompanionBridge.
    """

    def __new__(cls, *args, **kwargs):
        try:
            from pymc_core.companion.frame_server import (
                CompanionFrameServer as _Base,  # type: ignore[import-untyped]
            )

            # Dynamically create the subclass the first time it's needed
            if not hasattr(cls, "_dynamic_cls"):
                cls._dynamic_cls = type(
                    "RemoteTermCompanionFrameServer",
                    (_Base,),
                    {
                        "__init__": cls._init,
                        "_persist_prefs": cls._persist_prefs,
                        "_load_prefs": cls._load_prefs,
                    },
                )
            return object.__new__(cls._dynamic_cls)
        except ImportError as err:
            raise RuntimeError("pymc_core.companion is not available — SPI mode required") from err

    @staticmethod
    def _init(
        self,
        bridge,
        companion_name: str,
        port: int,
        bind_address: str,
        client_idle_timeout_sec: int | None,
        db: aiosqlite.Connection,
        companion_hash: str = "",
    ) -> None:
        from pymc_core.companion.frame_server import (
            CompanionFrameServer as _Base,  # type: ignore[import-untyped]
        )

        _Base.__init__(
            self,
            bridge=bridge,
            companion_hash=companion_hash,
            port=port,
            bind_address=bind_address,
            client_idle_timeout_sec=client_idle_timeout_sec,
            device_model="RemoteTerm-Companion",
        )
        self._db = db
        self._companion_name = companion_name

    @staticmethod
    async def _persist_prefs(self, prefs_data: dict) -> None:
        """Persist NodePrefs as JSON to companion_prefs table."""
        prefs_json = _to_json_safe_str(prefs_data)
        await self._db.execute(
            """INSERT INTO companion_prefs (companion_name, prefs_json, updated_at)
               VALUES (?, ?, unixepoch())
               ON CONFLICT(companion_name) DO UPDATE SET prefs_json=excluded.prefs_json, updated_at=excluded.updated_at""",
            (self._companion_name, prefs_json),
        )
        await self._db.commit()

    @staticmethod
    async def _load_prefs(self) -> dict | None:
        """Load NodePrefs JSON blob from companion_prefs table."""
        async with self._db.execute(
            "SELECT prefs_json FROM companion_prefs WHERE companion_name = ?",
            (self._companion_name,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            logger.warning("companion '%s': corrupt prefs JSON, ignoring", self._companion_name)
            return None


def _to_json_safe_str(value: Any) -> str:
    def _convert(v: Any) -> Any:
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, bytes):
            return v.hex()
        if isinstance(v, (list, tuple)):
            return [_convert(i) for i in v]
        if isinstance(v, dict):
            return {k: _convert(val) for k, val in v.items()}
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            return {f.name: _convert(getattr(v, f.name)) for f in dataclasses.fields(v)}
        return str(v)

    return json.dumps(_convert(value))


def _check_port_available(port: int, bind_address: str) -> bool:
    """Return True if the port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((bind_address if bind_address != "0.0.0.0" else "127.0.0.1", port))
        return True
    except OSError:
        return False


class VirtualCompanion:
    """Lifecycle wrapper for a single virtual TCP companion."""

    def __init__(
        self,
        companion_config: CompanionConfig,
        identity: Any,  # pymc_core LocalIdentity
        packet_injector: Callable,
        register_raw_rx_subscriber: Callable,
        db: aiosqlite.Connection,
    ) -> None:
        self.config = companion_config
        self._identity = identity
        self._packet_injector = packet_injector
        self._register_raw_rx_subscriber = register_raw_rx_subscriber
        self._db = db

        pub_key: bytes = identity.get_public_key()
        self._public_key_hex = pub_key.hex()
        self._public_key_prefix = self._public_key_hex[:12]
        companion_hash = self._public_key_hex[:2]  # first byte as 2-char hex

        self._bridge: Any = None
        self._frame_server: Any = None
        self._companion_hash = companion_hash
        self._server_task: asyncio.Task | None = None

        # Status tracking
        self._connected = False
        self._client_address: str | None = None

    async def start(self) -> None:
        """Set up CompanionBridge and start TCP frame server."""
        try:
            from pymc_core.companion import CompanionBridge  # type: ignore[import-untyped]
        except ImportError as err:
            raise RuntimeError("pymc_core.companion is not available — SPI mode required") from err

        self._bridge = CompanionBridge(
            identity=self._identity,
            packet_injector=self._packet_injector,
            node_name=self.config.name,
            adv_type=1,  # ADV_TYPE_CHAT
        )

        # Register bridge to receive parsed incoming packets destined for this identity.
        # add_raw_packet_subscriber calls back with (pkt, data); we only need pkt.
        async def _on_parsed_packet(pkt: Any, data: bytes) -> None:
            await self._bridge.process_received_packet(pkt)

        self._register_raw_rx_subscriber(_on_parsed_packet)

        timeout = self.config.tcp_timeout if self.config.tcp_timeout > 0 else None

        self._frame_server = _RemoteTermCompanionFrameServer(
            bridge=self._bridge,
            companion_name=self.config.name,
            port=self.config.tcp_port,
            bind_address=self.config.bind_address,
            client_idle_timeout_sec=timeout,
            db=self._db,
            companion_hash=self._companion_hash,
        )

        # Patch connection state tracking onto the frame server
        _patch_connection_tracking(self._frame_server, self)

        self._server_task = asyncio.create_task(
            self._frame_server.start(),
            name=f"companion-{self.config.name}-tcp",
        )
        logger.info(
            "Companion '%s' TCP server starting on %s:%d",
            self.config.name,
            self.config.bind_address,
            self.config.tcp_port,
        )

    async def stop(self) -> None:
        if self._frame_server is not None:
            try:
                await self._frame_server.stop()
            except Exception:
                pass
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        logger.info("Companion '%s' stopped", self.config.name)

    def get_status(self) -> CompanionStatus:
        return CompanionStatus(
            name=self.config.name,
            public_key_prefix=self._public_key_prefix,
            tcp_port=self.config.tcp_port,
            bind_address=self.config.bind_address,
            connected=self._connected,
            client_address=self._client_address,
        )


def _patch_connection_tracking(frame_server: Any, companion: VirtualCompanion) -> None:
    """Monkey-patch connection/disconnection callbacks onto frame_server for status tracking."""
    original_on_connect = getattr(frame_server, "_on_client_connected", None)
    original_on_disconnect = getattr(frame_server, "_on_client_disconnected", None)

    async def on_connect(addr: str) -> None:
        companion._connected = True
        companion._client_address = addr
        logger.info("Companion '%s': client connected from %s", companion.config.name, addr)
        if original_on_connect:
            await original_on_connect(addr)

    async def on_disconnect() -> None:
        companion._connected = False
        companion._client_address = None
        logger.info("Companion '%s': client disconnected", companion.config.name)
        if original_on_disconnect:
            await original_on_disconnect()

    frame_server._on_client_connected = on_connect
    frame_server._on_client_disconnected = on_disconnect
