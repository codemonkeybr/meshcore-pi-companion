"""VirtualManager — lifecycle controller for all virtual rooms and companions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.virtual.models import (
    CompanionConfig,
    CompanionStatus,
    RoomConfig,
    RoomStatus,
)

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class VirtualManager:
    """Manages startup, operation, and shutdown of virtual rooms and TCP companions."""

    def __init__(self) -> None:
        self._rooms: list[Any] = []  # list[RoomServer]
        self._companions: list[Any] = []  # list[VirtualCompanion]
        self._started = False

    async def start(
        self,
        backend: Any,
        db: aiosqlite.Connection,
        config: dict,
        config_path: Path | None = None,
    ) -> None:
        """Start all configured virtual rooms and companions.

        Silently skips if backend is not SpiBackend or no rooms/companions are configured.
        Raises RuntimeError on port conflicts (hard failure — app should not start in
        a misconfigured state).
        """
        from app.backends.spi_backend import SpiBackend

        if not isinstance(backend, SpiBackend):
            logger.info("VirtualManager: not SPI backend — virtual rooms/companions disabled")
            return

        # Auto-generate missing identity keys and write back to config
        if config_path is not None:
            from app.spi_identity import ensure_virtual_identity_keys

            config = ensure_virtual_identity_keys(config, config_path)

        await self._start_rooms(backend, db, config)
        await self._start_companions(backend, db, config)
        self._started = True

    async def stop(self) -> None:
        for room in self._rooms:
            try:
                await room.stop()
            except Exception:
                logger.exception(
                    "Error stopping room '%s'", getattr(room, "config", {}).get("name", "?")
                )
        for companion in self._companions:
            try:
                await companion.stop()
            except Exception:
                logger.exception(
                    "Error stopping companion '%s'",
                    getattr(companion, "config", {}).get("name", "?"),
                )
        self._rooms.clear()
        self._companions.clear()
        self._started = False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def room_statuses(self) -> list[RoomStatus]:
        statuses = []
        for room in self._rooms:
            try:
                statuses.append(await room.get_status_async())
            except Exception:
                statuses.append(room.get_status())
        return statuses

    def companion_statuses(self) -> list[CompanionStatus]:
        return [c.get_status() for c in self._companions]

    # ------------------------------------------------------------------
    # Internal startup helpers
    # ------------------------------------------------------------------

    async def _start_rooms(self, backend: Any, db: aiosqlite.Connection, config: dict) -> None:
        from pymc_core.protocol import LocalIdentity  # type: ignore[import-not-found]

        from app.virtual.room_server import RoomServer

        room_configs = config.get("virtual_rooms") or []
        if not room_configs:
            return

        for raw in room_configs:
            try:
                room_cfg = RoomConfig(**raw)
            except Exception:
                logger.exception("VirtualManager: invalid room config %r", raw)
                continue

            if not room_cfg.identity_key:
                logger.error(
                    "VirtualManager: room '%s' has no identity_key — skipping", room_cfg.name
                )
                continue

            try:
                seed = bytes.fromhex(room_cfg.identity_key)
                identity = LocalIdentity(seed=seed)
            except Exception:
                logger.exception(
                    "VirtualManager: failed to create identity for room '%s'", room_cfg.name
                )
                continue

            room = RoomServer(
                room_config=room_cfg,
                identity=identity,
                packet_injector=backend.packet_injector,
                db=db,
                register_dispatcher_handler=backend.register_dispatcher_handler,
            )
            await room.start()
            self._rooms.append(room)
            logger.info("VirtualManager: room '%s' started", room_cfg.name)

        if self._rooms:
            backend.register_raw_rx_subscriber(self._room_txt_msg_handler)

    async def _room_txt_msg_handler(self, pkt: Any, data: bytes, analysis: Any = None) -> None:
        """Intercept TXT_MSG packets destined for virtual room identities and route to handle_text."""
        try:
            from pymc_core.protocol.constants import (
                PAYLOAD_TYPE_TXT_MSG,  # type: ignore[import-not-found]
            )
            from pymc_core.protocol.crypto import CryptoUtils  # type: ignore[import-not-found]
        except ImportError:
            return

        try:
            if pkt.get_payload_type() != PAYLOAD_TYPE_TXT_MSG:
                return
            if len(pkt.payload) < 3:
                return

            dest_hash: int = pkt.payload[0]
            src_hash: int = pkt.payload[1]
            payload: bytes = bytes(pkt.payload[2:])

            room = None
            for r in self._rooms:
                if r._hash_byte == dest_hash:
                    room = r
                    break
            if room is None:
                return

            # Find ACL entry by first byte of client public key
            entry = None
            for e in room._acl.values():
                client_bytes = bytes.fromhex(e.client_key_hex)
                if client_bytes[0] == src_hash:
                    entry = e
                    break

            if entry is None or entry.shared_secret is None:
                logger.debug(
                    "Room '%s': TXT_MSG from unknown src_hash=0x%02x — not authenticated",
                    room.config.name,
                    src_hash,
                )
                return

            aes_key = entry.shared_secret[:16]
            decrypted = CryptoUtils.mac_then_decrypt(aes_key, entry.shared_secret, payload)

            if len(decrypted) < 5:
                return

            # decrypted layout: timestamp(4) + flags(1) + message_body
            text_bytes = decrypted[5:]
            try:
                text = text_bytes.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("Room '%s': TXT_MSG body is not valid UTF-8", room.config.name)
                return

            sender_key = bytes.fromhex(entry.client_key_hex)
            await room.handle_text(sender_key, text)
        except Exception:
            logger.exception("VirtualManager: error in room TXT_MSG handler")

    async def _start_companions(self, backend: Any, db: aiosqlite.Connection, config: dict) -> None:
        from pymc_core.protocol import LocalIdentity  # type: ignore[import-not-found]

        from app.virtual.companion import VirtualCompanion, _check_port_available

        companion_configs = config.get("virtual_companions") or []

        # Validate port uniqueness before starting anything
        seen_ports: set[int] = set()
        for raw in companion_configs:
            port = raw.get("tcp_port")
            if port in seen_ports:
                raise RuntimeError(
                    f"VirtualManager: duplicate TCP port {port} in virtual_companions config — "
                    "each companion must have a unique port. Fix config.yaml and restart."
                )
            seen_ports.add(port)

        for raw in companion_configs:
            try:
                comp_cfg = CompanionConfig(**raw)
            except Exception:
                logger.exception("VirtualManager: invalid companion config %r", raw)
                continue

            if not comp_cfg.identity_key:
                logger.error(
                    "VirtualManager: companion '%s' has no identity_key — skipping", comp_cfg.name
                )
                continue

            # Check port availability
            if not _check_port_available(comp_cfg.tcp_port, comp_cfg.bind_address):
                raise RuntimeError(
                    f"VirtualManager: TCP port {comp_cfg.tcp_port} is already in use "
                    f"(companion '{comp_cfg.name}'). Fix the port conflict and restart."
                )

            try:
                seed = bytes.fromhex(comp_cfg.identity_key)
                identity = LocalIdentity(seed=seed)
            except Exception:
                logger.exception(
                    "VirtualManager: failed to create identity for companion '%s'", comp_cfg.name
                )
                continue

            companion = VirtualCompanion(
                companion_config=comp_cfg,
                identity=identity,
                packet_injector=backend.packet_injector,
                register_raw_rx_subscriber=backend.register_raw_rx_subscriber,
                db=db,
            )
            await companion.start()
            self._companions.append(companion)
            logger.info(
                "VirtualManager: companion '%s' started on port %d",
                comp_cfg.name,
                comp_cfg.tcp_port,
            )


# Process-global singleton
virtual_manager = VirtualManager()
