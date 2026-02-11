import asyncio
import glob
import logging
import platform
from contextlib import asynccontextmanager
from pathlib import Path

from meshcore import MeshCore

from app.config import settings

logger = logging.getLogger(__name__)


class RadioOperationError(RuntimeError):
    """Base class for shared radio operation lock errors."""


class RadioOperationBusyError(RadioOperationError):
    """Raised when a non-blocking radio operation cannot acquire the lock."""


@asynccontextmanager
async def _noop_context():
    """No-op async context manager for optional nesting."""
    yield


def detect_serial_devices() -> list[str]:
    """Detect available serial devices based on platform."""
    devices: list[str] = []
    system = platform.system()

    if system == "Darwin":
        # macOS: Use /dev/cu.* devices (callout devices, preferred over tty.*)
        patterns = [
            "/dev/cu.usb*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.SLAB_USBtoUART*",
        ]
        for pattern in patterns:
            devices.extend(glob.glob(pattern))
        devices.sort()
    else:
        # Linux: Prefer /dev/serial/by-id/ for persistent naming
        by_id_path = Path("/dev/serial/by-id")
        if by_id_path.is_dir():
            devices.extend(str(p) for p in by_id_path.iterdir())

        # Also check /dev/ttyACM* and /dev/ttyUSB* as fallback
        resolved_paths = set()
        for dev in devices:
            try:
                resolved_paths.add(str(Path(dev).resolve()))
            except OSError:
                pass

        for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*"]:
            for dev in glob.glob(pattern):
                try:
                    if str(Path(dev).resolve()) not in resolved_paths:
                        devices.append(dev)
                except OSError:
                    devices.append(dev)

        devices.sort()

    return devices


async def test_serial_device(port: str, baudrate: int, timeout: float = 3.0) -> bool:
    """Test if a MeshCore radio responds on the given serial port."""
    try:
        logger.debug("Testing serial device %s", port)
        mc = await asyncio.wait_for(
            MeshCore.create_serial(port=port, baudrate=baudrate),
            timeout=timeout,
        )

        # Check if we got valid self_info (indicates successful communication)
        if mc.is_connected and mc.self_info:
            logger.debug("Device %s responded with valid self_info", port)
            await mc.disconnect()
            return True

        await mc.disconnect()
        return False
    except asyncio.TimeoutError:
        logger.debug("Device %s timed out", port)
        return False
    except Exception as e:
        logger.debug("Device %s failed: %s", port, e)
        return False


async def find_radio_port(baudrate: int) -> str | None:
    """Find the first serial port with a responding MeshCore radio."""
    devices = detect_serial_devices()

    if not devices:
        logger.warning("No serial devices found")
        return None

    logger.info("Found %d serial device(s), testing for MeshCore radio...", len(devices))

    for device in devices:
        if await test_serial_device(device, baudrate):
            logger.info("Found MeshCore radio at %s", device)
            return device

    logger.warning("No MeshCore radio found on any serial device")
    return None


class RadioManager:
    """Manages the MeshCore radio connection."""

    def __init__(self):
        self._meshcore: MeshCore | None = None
        self._connection_info: str | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: bool = False
        self._reconnect_lock: asyncio.Lock | None = None
        self._operation_lock: asyncio.Lock | None = None
        self._setup_lock: asyncio.Lock | None = None
        self._setup_in_progress: bool = False

    async def _acquire_operation_lock(
        self,
        name: str,
        *,
        blocking: bool,
    ) -> None:
        """Acquire the shared radio operation lock."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()

        if not blocking:
            if self._operation_lock.locked():
                raise RadioOperationBusyError(f"Radio is busy (operation: {name})")
            await self._operation_lock.acquire()
        else:
            await self._operation_lock.acquire()

        logger.debug("Acquired radio operation lock (%s)", name)

    def _release_operation_lock(self, name: str) -> None:
        """Release the shared radio operation lock."""
        if self._operation_lock and self._operation_lock.locked():
            self._operation_lock.release()
            logger.debug("Released radio operation lock (%s)", name)
        else:
            logger.error("Attempted to release unlocked radio operation lock (%s)", name)

    @asynccontextmanager
    async def radio_operation(
        self,
        name: str,
        *,
        pause_polling: bool = False,
        suspend_auto_fetch: bool = False,
        blocking: bool = True,
        meshcore: MeshCore | None = None,
    ):
        """Acquire shared radio lock and optionally pause polling / auto-fetch.

        Args:
            name: Human-readable operation name for logs/errors.
            pause_polling: Pause fallback message polling while held.
            suspend_auto_fetch: Stop MeshCore auto message fetching while held.
            blocking: If False, fail immediately when lock is held.
            meshcore: Optional explicit MeshCore instance for auto-fetch control.
        """
        await self._acquire_operation_lock(name, blocking=blocking)

        poll_context = _noop_context()
        if pause_polling:
            from app.radio_sync import pause_polling as pause_polling_context

            poll_context = pause_polling_context()

        mc = meshcore or self._meshcore
        auto_fetch_paused = False

        try:
            async with poll_context:
                if suspend_auto_fetch and mc is not None:
                    await mc.stop_auto_message_fetching()
                    auto_fetch_paused = True
                yield
        finally:
            try:
                if auto_fetch_paused and mc is not None:
                    try:
                        await mc.start_auto_message_fetching()
                    except Exception as e:
                        logger.warning("Failed to restart auto message fetching (%s): %s", name, e)
            finally:
                self._release_operation_lock(name)

    async def post_connect_setup(self) -> None:
        """Full post-connection setup: handlers, key export, sync, advertisements, polling.

        Called after every successful connection or reconnection.
        Idempotent — safe to call repeatedly (periodic tasks have start guards).
        """
        from app.event_handlers import register_event_handlers
        from app.keystore import export_and_store_private_key
        from app.radio_sync import (
            drain_pending_messages,
            send_advertisement,
            start_message_polling,
            start_periodic_advert,
            start_periodic_sync,
            sync_and_offload_all,
            sync_radio_time,
        )

        if not self._meshcore:
            return

        if self._setup_lock is None:
            self._setup_lock = asyncio.Lock()

        async with self._setup_lock:
            if not self._meshcore:
                return
            self._setup_in_progress = True
            try:
                register_event_handlers(self._meshcore)
                await export_and_store_private_key(self._meshcore)

                # Sync radio clock with system time
                await sync_radio_time()

                # Sync contacts/channels from radio to DB and clear radio
                logger.info("Syncing and offloading radio data...")
                result = await sync_and_offload_all()
                logger.info("Sync complete: %s", result)

                # Start periodic sync (idempotent)
                start_periodic_sync()

                # Send advertisement to announce our presence (if enabled and not throttled)
                if await send_advertisement():
                    logger.info("Advertisement sent")
                else:
                    logger.debug("Advertisement skipped (disabled or throttled)")

                # Start periodic advertisement (idempotent)
                start_periodic_advert()

                await self._meshcore.start_auto_message_fetching()
                logger.info("Auto message fetching started")

                # Drain any messages that were queued before we connected
                drained = await drain_pending_messages()
                if drained > 0:
                    logger.info("Drained %d pending message(s)", drained)

                # Start periodic message polling as fallback (idempotent)
                start_message_polling()
            finally:
                self._setup_in_progress = False

        logger.info("Post-connect setup complete")

    @property
    def meshcore(self) -> MeshCore | None:
        return self._meshcore

    @property
    def connection_info(self) -> str | None:
        return self._connection_info

    @property
    def is_connected(self) -> bool:
        return self._meshcore is not None and self._meshcore.is_connected

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnect_lock is not None and self._reconnect_lock.locked()

    @property
    def is_setup_in_progress(self) -> bool:
        return self._setup_in_progress

    async def connect(self) -> None:
        """Connect to the radio using the configured transport."""
        if self._meshcore is not None:
            await self.disconnect()

        connection_type = settings.connection_type
        if connection_type == "tcp":
            await self._connect_tcp()
        elif connection_type == "ble":
            await self._connect_ble()
        else:
            await self._connect_serial()

    async def _connect_serial(self) -> None:
        """Connect to the radio over serial."""
        port = settings.serial_port

        # Auto-detect if no port specified
        if not port:
            logger.info("No serial port specified, auto-detecting...")
            port = await find_radio_port(settings.serial_baudrate)
            if not port:
                raise RuntimeError("No MeshCore radio found. Please specify MESHCORE_SERIAL_PORT.")

        logger.debug(
            "Connecting to radio at %s (baud %d)",
            port,
            settings.serial_baudrate,
        )
        self._meshcore = await MeshCore.create_serial(
            port=port,
            baudrate=settings.serial_baudrate,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        self._connection_info = f"Serial: {port}"
        self._last_connected = True
        logger.debug("Serial connection established")

    async def _connect_tcp(self) -> None:
        """Connect to the radio over TCP."""
        host = settings.tcp_host
        port = settings.tcp_port

        logger.debug("Connecting to radio at %s:%d (TCP)", host, port)
        self._meshcore = await MeshCore.create_tcp(
            host=host,
            port=port,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        self._connection_info = f"TCP: {host}:{port}"
        self._last_connected = True
        logger.debug("TCP connection established")

    async def _connect_ble(self) -> None:
        """Connect to the radio over BLE."""
        address = settings.ble_address
        pin = settings.ble_pin

        logger.debug("Connecting to radio at %s (BLE)", address)
        self._meshcore = await MeshCore.create_ble(
            address=address,
            pin=pin,
            auto_reconnect=True,
            max_reconnect_attempts=15,
        )
        self._connection_info = f"BLE: {address}"
        self._last_connected = True
        logger.debug("BLE connection established")

    async def disconnect(self) -> None:
        """Disconnect from the radio."""
        if self._meshcore is not None:
            logger.debug("Disconnecting from radio")
            await self._meshcore.disconnect()
            self._meshcore = None
            logger.debug("Radio disconnected")

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the radio.

        Returns True if reconnection was successful, False otherwise.
        Uses a lock to prevent concurrent reconnection attempts.
        """
        from app.websocket import broadcast_error, broadcast_health

        # Lazily initialize lock (can't create in __init__ before event loop exists)
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()

        # Try to acquire lock without blocking to check if reconnect is in progress
        if self._reconnect_lock.locked():
            logger.debug("Reconnection already in progress")
            return False

        async with self._reconnect_lock:
            logger.info("Attempting to reconnect to radio...")

            try:
                # Disconnect if we have a stale connection
                if self._meshcore is not None:
                    try:
                        await self._meshcore.disconnect()
                    except Exception:
                        pass
                    self._meshcore = None

                # Try to connect (will auto-detect if no port specified)
                await self.connect()

                if self.is_connected:
                    logger.info("Radio reconnected successfully at %s", self._connection_info)
                    broadcast_health(True, self._connection_info)
                    return True
                else:
                    logger.warning("Reconnection failed: not connected after connect()")
                    return False

            except Exception as e:
                logger.warning("Reconnection failed: %s", e)
                broadcast_error("Reconnection failed", str(e))
                return False

    async def start_connection_monitor(self) -> None:
        """Start background task to monitor connection and auto-reconnect."""
        if self._reconnect_task is not None:
            return

        async def monitor_loop():
            from app.websocket import broadcast_health

            CHECK_INTERVAL_SECONDS = 5

            while True:
                try:
                    await asyncio.sleep(CHECK_INTERVAL_SECONDS)

                    current_connected = self.is_connected

                    # Detect status change
                    if self._last_connected and not current_connected:
                        # Connection lost
                        logger.warning("Radio connection lost, broadcasting status change")
                        broadcast_health(False, self._connection_info)
                        self._last_connected = False

                    if not current_connected:
                        # Attempt reconnection on every loop while disconnected
                        if not self.is_reconnecting and await self.reconnect():
                            await self.post_connect_setup()
                            self._last_connected = True

                    elif not self._last_connected and current_connected:
                        # Connection restored (might have reconnected automatically)
                        logger.info("Radio connection restored")
                        broadcast_health(True, self._connection_info)
                        self._last_connected = True

                except asyncio.CancelledError:
                    # Task is being cancelled, exit cleanly
                    break
                except Exception as e:
                    # Log error but continue monitoring - don't let the monitor die
                    logger.exception("Error in connection monitor, continuing: %s", e)

        self._reconnect_task = asyncio.create_task(monitor_loop())
        logger.info("Radio connection monitor started")

    async def stop_connection_monitor(self) -> None:
        """Stop the connection monitor task."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
            logger.info("Radio connection monitor stopped")


radio_manager = RadioManager()
