"""Virtual room server for RemoteTerm.

Ported from pyMC_Repeater's handler_helpers/room_server.py and adapted for
RemoteTerm's async model and aiosqlite database access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.virtual.models import RoomConfig, RoomStatus

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# Hard limits (from C++ simple_room_server and pyMC_Repeater)
MAX_CLIENTS_PER_ROOM = 50
MAX_MESSAGE_LENGTH = 160
MAX_POSTS_PER_CLIENT_PER_MINUTE = 10
MAX_PUSH_FAILURES = 3
INACTIVE_CLIENT_TIMEOUT = 3600  # 1 hour in seconds

# Push timing constants
PUSH_NOTIFY_DELAY_MS = 2000
SYNC_PUSH_INTERVAL_MS = 1200
POST_SYNC_DELAY_SECS = 6

# Global rate limiter — shared across all room instances
_global_rate_limiter: GlobalRateLimiter | None = None
_global_rate_lock = asyncio.Lock()
GLOBAL_MIN_GAP_BETWEEN_MESSAGES = 1.1  # seconds


class GlobalRateLimiter:
    """Enforces a minimum gap between consecutive radio transmissions."""

    def __init__(self, min_gap_seconds: float = GLOBAL_MIN_GAP_BETWEEN_MESSAGES) -> None:
        self.min_gap = min_gap_seconds
        self.lock = asyncio.Lock()
        self.last_release_time: float = 0.0

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_release_time
            if elapsed < self.min_gap:
                await asyncio.sleep(self.min_gap - elapsed)

    def release(self) -> None:
        self.last_release_time = time.monotonic()


async def _get_global_rate_limiter() -> GlobalRateLimiter:
    global _global_rate_limiter
    async with _global_rate_lock:
        if _global_rate_limiter is None:
            _global_rate_limiter = GlobalRateLimiter()
        return _global_rate_limiter


class _AclEntry:
    """In-memory ACL entry for a room client."""

    __slots__ = (
        "client_key_hex",
        "auth_level",
        "sync_since",
        "push_failures",
        "last_activity",
        "post_timestamps",
    )

    def __init__(
        self,
        client_key_hex: str,
        auth_level: int = 0,
        sync_since: int = 0,
        push_failures: int = 0,
        last_activity: float | None = None,
    ) -> None:
        self.client_key_hex = client_key_hex
        self.auth_level = auth_level
        self.sync_since = sync_since
        self.push_failures = push_failures
        self.last_activity = last_activity or time.time()
        self.post_timestamps: list[float] = []  # for rate limiting


class RoomServer:
    """Virtual room server that hosts a mesh message room identity."""

    def __init__(
        self,
        room_config: RoomConfig,
        identity: Any,  # pymc_core LocalIdentity
        packet_injector: Callable,
        db: aiosqlite.Connection,
        register_dispatcher_handler: Callable,
    ) -> None:
        self.config = room_config
        self.identity = identity
        self._packet_injector = packet_injector
        self._db = db
        self._register_dispatcher_handler = register_dispatcher_handler

        # Derive public key info
        pub_key: bytes = identity.get_public_key()
        self._public_key_hex = pub_key.hex()
        self._public_key_prefix = self._public_key_hex[:12]
        self._hash_byte: int = pub_key[0]

        # In-memory ACL: client_key_hex → _AclEntry
        self._acl: dict[str, _AclEntry] = {}

        # Sync loop state
        self._sync_task: asyncio.Task | None = None
        self._sync_event = asyncio.Event()
        self._running = False
        self._rate_limiter: GlobalRateLimiter | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register identity handlers and begin sync loop."""
        self._rate_limiter = await _get_global_rate_limiter()
        await self._load_acl_from_db()
        self._running = True

        try:
            from pymc_core.node.handlers.login_server import (
                LoginServerHandler,  # type: ignore[import-not-found]
            )

            handler = LoginServerHandler(
                local_identity=self.identity,
                authenticate_callback=self._authenticate_callback,
                on_login=self._on_login_packet,
            )
            self._register_dispatcher_handler(handler)
            logger.info(
                "Room '%s' registered LoginServerHandler (hash=0x%02x)",
                self.config.name,
                self._hash_byte,
            )
        except Exception:
            logger.exception("Room '%s': failed to register LoginServerHandler", self.config.name)

        await self._send_advert()
        self._sync_task = asyncio.create_task(
            self._sync_loop(), name=f"room-sync-{self.config.name}"
        )
        logger.info("Room '%s' started (key=%s…)", self.config.name, self._public_key_prefix)

    async def stop(self) -> None:
        self._running = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        logger.info("Room '%s' stopped", self.config.name)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _authenticate_callback(self, password: str) -> tuple[bool, int]:
        """Called by LoginServerHandler to verify a login password."""
        if password == self.config.admin_password:
            return True, 1  # admin
        if password == self.config.guest_password:
            return True, 0  # guest
        return False, 0

    async def _on_login_packet(self, client_key: bytes, auth_level: int) -> None:
        """Called by LoginServerHandler after successful authentication."""
        client_key_hex = client_key.hex()
        if len(self._acl) >= MAX_CLIENTS_PER_ROOM:
            logger.warning(
                "Room '%s': max clients reached, rejecting %s…",
                self.config.name,
                client_key_hex[:12],
            )
            return

        entry = _AclEntry(client_key_hex=client_key_hex, auth_level=auth_level)
        self._acl[client_key_hex] = entry
        await self._save_acl_entry(entry)
        logger.info(
            "Room '%s': client %s… authenticated (level=%d)",
            self.config.name,
            client_key_hex[:12],
            auth_level,
        )
        self._sync_event.set()

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_text(self, sender_key: bytes, text: str) -> None:
        """Handle an incoming text message from a mesh client."""
        sender_hex = sender_key.hex()
        entry = self._acl.get(sender_hex)
        if not entry:
            logger.debug(
                "Room '%s': ignoring text from unauthenticated sender %s…",
                self.config.name,
                sender_hex[:12],
            )
            return

        # Rate limit
        now = time.time()
        entry.post_timestamps = [t for t in entry.post_timestamps if now - t < 60]
        if len(entry.post_timestamps) >= MAX_POSTS_PER_CLIENT_PER_MINUTE:
            logger.warning("Room '%s': rate limit hit for %s…", self.config.name, sender_hex[:12])
            return

        # Size limit
        if len(text.encode()) > MAX_MESSAGE_LENGTH:
            logger.warning(
                "Room '%s': message too long from %s…", self.config.name, sender_hex[:12]
            )
            return

        entry.post_timestamps.append(now)
        entry.last_activity = now

        await self._store_message(sender_hex, text)
        self._sync_event.set()

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    async def _load_acl_from_db(self) -> None:
        async with self._db.execute(
            "SELECT client_key_hex, auth_level, sync_since, push_failures, last_activity "
            "FROM room_acl WHERE room_name = ?",
            (self.config.name,),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            entry = _AclEntry(
                client_key_hex=row[0],
                auth_level=row[1],
                sync_since=row[2],
                push_failures=row[3],
                last_activity=row[4],
            )
            self._acl[entry.client_key_hex] = entry
        logger.debug("Room '%s': loaded %d ACL entries from DB", self.config.name, len(self._acl))

    async def _save_acl_entry(self, entry: _AclEntry) -> None:
        await self._db.execute(
            """INSERT INTO room_acl (room_name, client_key_hex, auth_level, sync_since, push_failures, last_activity)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(room_name, client_key_hex) DO UPDATE SET
                   auth_level=excluded.auth_level,
                   sync_since=excluded.sync_since,
                   push_failures=excluded.push_failures,
                   last_activity=excluded.last_activity""",
            (
                self.config.name,
                entry.client_key_hex,
                entry.auth_level,
                entry.sync_since,
                entry.push_failures,
                entry.last_activity,
            ),
        )
        await self._db.commit()

    async def _store_message(self, sender_key_hex: str, text: str) -> None:
        await self._db.execute(
            "INSERT INTO room_messages (room_name, sender_key_hex, text) VALUES (?, ?, ?)",
            (self.config.name, sender_key_hex, text),
        )
        await self._db.commit()

    async def _get_messages_since(self, since_id: int) -> list[Any]:
        """Return (id, sender_key_hex, text) rows with id > since_id."""
        async with self._db.execute(
            "SELECT id, sender_key_hex, text FROM room_messages WHERE room_name = ? AND id > ? ORDER BY id ASC LIMIT 32",
            (self.config.name, since_id),
        ) as cur:
            return await cur.fetchall()

    async def _count_messages(self) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM room_messages WHERE room_name = ?", (self.config.name,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Sync loop
    # ------------------------------------------------------------------

    async def _sync_loop(self) -> None:
        """Round-robin push of new messages to all authenticated ACL clients."""
        while self._running:
            try:
                await asyncio.wait_for(self._sync_event.wait(), timeout=POST_SYNC_DELAY_SECS)
                self._sync_event.clear()
            except TimeoutError:
                pass

            if not self._running:
                break

            now = time.time()
            evict_keys: list[str] = []

            for client_key_hex, entry in list(self._acl.items()):
                # Evict inactive clients
                if now - entry.last_activity > INACTIVE_CLIENT_TIMEOUT:
                    evict_keys.append(client_key_hex)
                    logger.info(
                        "Room '%s': evicting %s… (inactive)", self.config.name, client_key_hex[:12]
                    )
                    continue

                messages = await self._get_messages_since(entry.sync_since)
                if not messages:
                    continue

                for msg_id, sender_hex, text in messages:
                    success = await self._push_message_to_client(client_key_hex, sender_hex, text)
                    if success:
                        entry.sync_since = msg_id
                        entry.push_failures = 0
                        entry.last_activity = time.time()
                    else:
                        entry.push_failures += 1
                        if entry.push_failures >= MAX_PUSH_FAILURES:
                            evict_keys.append(client_key_hex)
                            logger.warning(
                                "Room '%s': evicting %s… (push failures)",
                                self.config.name,
                                client_key_hex[:12],
                            )
                        break

                await self._save_acl_entry(entry)

            for key in evict_keys:
                self._acl.pop(key, None)
                await self._db.execute(
                    "DELETE FROM room_acl WHERE room_name = ? AND client_key_hex = ?",
                    (self.config.name, key),
                )
            if evict_keys:
                await self._db.commit()

    async def _push_message_to_client(
        self, client_key_hex: str, sender_hex: str, text: str
    ) -> bool:
        """Transmit a message to one client via the rate-limited radio."""
        if not self._packet_injector or not self._rate_limiter:
            return False
        try:
            from pymc_core.protocol import PacketBuilder  # type: ignore[import-not-found]

            payload = text.encode("utf-8")
            dest_key = bytes.fromhex(client_key_hex)

            packet = PacketBuilder.build_text_message(
                sender_identity=self.identity,
                dest_public_key=dest_key,
                text=payload,
            )

            await self._rate_limiter.acquire()
            try:
                await self._packet_injector(packet)
                return True
            finally:
                self._rate_limiter.release()
        except Exception:
            logger.exception(
                "Room '%s': error pushing to %s…", self.config.name, client_key_hex[:12]
            )
            return False

    # ------------------------------------------------------------------
    # Advertisement
    # ------------------------------------------------------------------

    async def _send_advert(self) -> None:
        """Inject a room-type advertisement packet onto the mesh."""
        if not self._packet_injector:
            return
        try:
            from pymc_core.protocol import PacketBuilder  # type: ignore[import-not-found]

            lat = self.config.latitude
            lon = self.config.longitude
            has_location = lat != 0.0 or lon != 0.0

            packet = PacketBuilder.build_advert(
                identity=self.identity,
                name=self.config.name,
                adv_type=3,  # ADV_TYPE_ROOM
                lat=lat if has_location else None,
                lon=lon if has_location else None,
            )
            await self._packet_injector(packet)
            logger.info("Room '%s': advertisement sent", self.config.name)
        except Exception:
            logger.exception("Room '%s': failed to send advertisement", self.config.name)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> RoomStatus:
        return RoomStatus(
            name=self.config.name,
            public_key_prefix=self._public_key_prefix,
            client_count=len(self._acl),
            message_count=0,  # filled async by manager if needed
            running=self._running,
        )

    async def get_status_async(self) -> RoomStatus:
        count = await self._count_messages()
        return RoomStatus(
            name=self.config.name,
            public_key_prefix=self._public_key_prefix,
            client_count=len(self._acl),
            message_count=count,
            running=self._running,
        )
