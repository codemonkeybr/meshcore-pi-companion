"""Abstract RadioBackend interface.

Defines the contract that all radio backends must implement.  The existing
meshcore client library is wrapped by ``ClientBackend``; a future SPI
backend will provide ``SpiBackend``.

Result objects returned by command methods carry ``.type`` (an
``meshcore.EventType`` value) and ``.payload`` (a dict or other value),
matching the shape produced by the meshcore library.  Phase 2 backends
must produce compatible result objects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshcore import EventType


class RadioBackend(ABC):
    """Abstract interface for all radio communication backends."""

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly shut down the connection / radio hardware."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the backend is connected and operational."""

    @property
    @abstractmethod
    def self_info(self) -> dict[str, Any] | None:
        """Node information dict (public_key, name, lat, lon, tx_power, …).

        Returns ``None`` when not yet available.
        """

    @abstractmethod
    async def start_auto_message_fetching(self) -> None:
        """Begin automatic background message polling (meshcore-specific).

        SPI backends that receive messages via callbacks may treat this as
        a no-op.
        """

    @abstractmethod
    async def stop_auto_message_fetching(self) -> None:
        """Pause automatic background message polling."""

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_contacts(self) -> Any:
        """Retrieve all contacts.

        Returns a result with ``.type`` and ``.payload`` (dict of
        ``{public_key: contact_data}``).
        """

    @abstractmethod
    async def add_contact(self, contact_dict: dict[str, Any]) -> Any:
        """Add or update a contact on the radio.

        *contact_dict* has the shape produced by ``Contact.to_radio_dict()``.
        Returns a result with ``.type`` / ``.payload``.
        """

    @abstractmethod
    async def remove_contact(self, contact_data: Any) -> Any:
        """Remove a contact.

        *contact_data* is the radio-side contact object (as returned by
        ``get_contact_by_key_prefix``).
        """

    @abstractmethod
    def get_contact_by_key_prefix(self, prefix: str) -> Any:
        """Look up a contact by public-key prefix (sync).

        Returns the radio-side contact object, or ``None``.
        """

    @abstractmethod
    def evict_contact_from_cache(self, public_key: str) -> None:
        """Remove a contact from the backend's in-memory cache.

        For the meshcore backend this pops from ``mc._contacts``.  SPI
        backends can treat this as a no-op or operate on their own cache.
        """

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_channel(self, idx: int) -> Any:
        """Get channel info at *idx*.

        Returns a result with ``.type`` (``CHANNEL_INFO`` on success) and
        ``.payload``.
        """

    @abstractmethod
    async def set_channel(
        self,
        *,
        channel_idx: int,
        channel_name: str,
        channel_secret: bytes,
    ) -> Any:
        """Set channel *channel_idx* to the given name and secret."""

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_msg(
        self,
        dst: Any,
        msg: str,
        *,
        timestamp: int | None = None,
    ) -> Any:
        """Send a direct message.

        *dst* is the radio-side contact object.
        Returns a result whose ``.payload`` may contain ``expected_ack``
        and ``suggested_timeout``.
        """

    @abstractmethod
    async def send_cmd(self, dst: Any, cmd: str) -> Any:
        """Send a CLI command to a repeater contact.

        *dst* is a public key string.
        """

    @abstractmethod
    async def send_chan_msg(
        self,
        *,
        chan: int,
        msg: str,
        timestamp: int | None = None,
    ) -> Any:
        """Send a channel message on slot *chan*."""

    @abstractmethod
    async def get_msg(self, timeout: float = 2.0) -> Any:
        """Poll for the next pending message.

        Returns a result whose ``.type`` is ``NO_MORE_MSGS`` when the
        queue is empty, or ``CONTACT_MSG_RECV`` / ``CHANNEL_MSG_RECV``
        for a message, or ``ERROR``.
        """

    # ------------------------------------------------------------------
    # Radio configuration
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_advert(self, *, flood: bool) -> Any:
        """Broadcast an advertisement."""

    @abstractmethod
    async def set_time(self, unix_ts: int) -> Any:
        """Sync the radio clock to *unix_ts*."""

    @abstractmethod
    async def send_device_query(self) -> Any:
        """Query device information.

        Returns a result whose ``.payload`` may include
        ``path_hash_mode`` and other firmware details.
        """

    @abstractmethod
    async def set_name(self, name: str) -> Any:
        """Set the node name."""

    @abstractmethod
    async def set_coords(self, *, lat: float, lon: float) -> Any:
        """Set GPS coordinates for advertisements."""

    @abstractmethod
    async def set_tx_power(self, *, val: int) -> Any:
        """Set transmit power in dBm."""

    @abstractmethod
    async def set_radio(
        self,
        *,
        freq: float,
        bw: float,
        sf: int,
        cr: int,
    ) -> Any:
        """Set LoRa radio parameters."""

    @abstractmethod
    async def set_flood_scope(self, scope: str) -> Any:
        """Set the regional flood scope.  Pass ``""`` to disable."""

    @abstractmethod
    async def set_path_hash_mode(self, mode: int) -> Any:
        """Set the path hash mode (0/1/2 = 1/2/3-byte hop IDs)."""

    @abstractmethod
    async def send_appstart(self) -> Any:
        """Trigger a fresh SELF_INFO from the radio."""

    @abstractmethod
    async def reboot(self) -> None:
        """Reboot the radio hardware."""

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    @abstractmethod
    async def export_private_key(self) -> Any:
        """Export the node's private key.

        Returns a result whose ``.type`` is ``PRIVATE_KEY`` on success.
        """

    @abstractmethod
    async def import_private_key(self, key: bytes) -> Any:
        """Import a private key onto the radio."""

    # ------------------------------------------------------------------
    # Repeater operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_login(self, public_key: str, password: str) -> Any:
        """Log in to a repeater."""

    @abstractmethod
    async def req_status_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        """Request repeater status (synchronous wait)."""

    @abstractmethod
    async def req_telemetry_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        """Request repeater telemetry (synchronous wait)."""

    @abstractmethod
    async def fetch_all_neighbours(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        """Fetch all neighbours from a repeater (synchronous wait)."""

    @abstractmethod
    async def req_acl_sync(
        self,
        public_key: str,
        timeout: int = 10,
        min_timeout: int = 5,
    ) -> Any:
        """Request repeater ACL (synchronous wait)."""

    @abstractmethod
    async def send_trace(self, *, path: str, tag: int) -> Any:
        """Send a trace packet."""

    @abstractmethod
    async def wait_for_event(
        self,
        event_type: EventType,
        *,
        attribute_filters: dict[str, Any] | None = None,
        timeout: float = 15,
    ) -> Any:
        """Wait for a specific event (e.g. TRACE_DATA).

        Returns the event, or ``None`` on timeout.
        """

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_stats_core(self) -> Any:
        """Fetch core statistics from the radio."""

    @abstractmethod
    async def get_stats_radio(self) -> Any:
        """Fetch radio statistics."""

    # ------------------------------------------------------------------
    # Events / subscriptions
    # ------------------------------------------------------------------

    @abstractmethod
    def subscribe(self, event_type: EventType, handler: Any) -> Any:
        """Subscribe to radio events.

        Returns a subscription object with an ``unsubscribe()`` method.
        """

    # ------------------------------------------------------------------
    # Backend-specific helpers
    # ------------------------------------------------------------------

    @abstractmethod
    async def query_path_hash_mode(self) -> tuple[int, bool]:
        """Detect the path hash mode supported by this backend.

        Returns ``(mode, supported)`` where *mode* is 0/1/2 and
        *supported* indicates whether the backend allows changing it.
        """
