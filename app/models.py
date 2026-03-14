from typing import Literal

from pydantic import BaseModel, Field

from app.path_utils import normalize_contact_route


class ContactUpsert(BaseModel):
    """Typed write contract for contacts persisted to SQLite."""

    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0
    flags: int = 0
    last_path: str | None = None
    last_path_len: int = -1
    out_path_hash_mode: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool | None = None
    last_contacted: int | None = None
    first_seen: int | None = None

    @classmethod
    def from_contact(cls, contact: "Contact", **changes) -> "ContactUpsert":
        return cls.model_validate(
            {
                **contact.model_dump(exclude={"last_read_at"}),
                **changes,
            }
        )

    @classmethod
    def from_radio_dict(
        cls, public_key: str, radio_data: dict, on_radio: bool = False
    ) -> "ContactUpsert":
        """Convert radio contact data to the contact-row write shape."""
        last_path, last_path_len, out_path_hash_mode = normalize_contact_route(
            radio_data.get("out_path"),
            radio_data.get("out_path_len", -1),
            radio_data.get(
                "out_path_hash_mode",
                -1 if radio_data.get("out_path_len", -1) == -1 else 0,
            ),
        )
        return cls(
            public_key=public_key,
            name=radio_data.get("adv_name"),
            type=radio_data.get("type", 0),
            flags=radio_data.get("flags", 0),
            last_path=last_path,
            last_path_len=last_path_len,
            out_path_hash_mode=out_path_hash_mode,
            lat=radio_data.get("adv_lat"),
            lon=radio_data.get("adv_lon"),
            last_advert=radio_data.get("last_advert"),
            on_radio=on_radio,
        )


class Contact(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0  # 0=unknown, 1=client, 2=repeater, 3=room, 4=sensor
    flags: int = 0
    last_path: str | None = None
    last_path_len: int = -1
    out_path_hash_mode: int = 0
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool = False
    last_contacted: int | None = None  # Last time we sent/received a message
    last_read_at: int | None = None  # Server-side read state tracking
    first_seen: int | None = None

    def has_route_override(self) -> bool:
        return self.route_override_len is not None

    def effective_route(self) -> tuple[str, int, int]:
        if self.has_route_override():
            return normalize_contact_route(
                self.route_override_path,
                self.route_override_len,
                self.route_override_hash_mode,
            )
        return normalize_contact_route(
            self.last_path,
            self.last_path_len,
            self.out_path_hash_mode,
        )

    def to_radio_dict(self) -> dict:
        """Convert to the dict format expected by meshcore radio commands.

        The radio API uses different field names (adv_name, out_path, etc.)
        than our database schema (name, last_path, etc.).
        """
        last_path, last_path_len, out_path_hash_mode = self.effective_route()
        return {
            "public_key": self.public_key,
            "adv_name": self.name or "",
            "type": self.type,
            "flags": self.flags,
            "out_path": last_path,
            "out_path_len": last_path_len,
            "out_path_hash_mode": out_path_hash_mode,
            "adv_lat": self.lat if self.lat is not None else 0.0,
            "adv_lon": self.lon if self.lon is not None else 0.0,
            "last_advert": self.last_advert if self.last_advert is not None else 0,
        }

    def to_upsert(self, **changes) -> ContactUpsert:
        """Convert the stored contact to the repository's write contract."""
        return ContactUpsert.from_contact(self, **changes)

    @staticmethod
    def from_radio_dict(public_key: str, radio_data: dict, on_radio: bool = False) -> dict:
        """Backward-compatible dict wrapper over ContactUpsert.from_radio_dict()."""
        return ContactUpsert.from_radio_dict(
            public_key,
            radio_data,
            on_radio=on_radio,
        ).model_dump()


class CreateContactRequest(BaseModel):
    """Request to create a new contact."""

    public_key: str = Field(min_length=64, max_length=64, description="Public key (64-char hex)")
    name: str | None = Field(default=None, description="Display name for the contact")
    try_historical: bool = Field(
        default=False,
        description="Attempt to decrypt historical DM packets for this contact",
    )


class ContactRoutingOverrideRequest(BaseModel):
    """Request to set, force, or clear a contact routing override."""

    route: str = Field(
        description=(
            "Blank clears the override and resets learned routing to flood, "
            '"-1" forces flood, "0" forces direct, and explicit routes are '
            "comma-separated 1/2/3-byte hop hex values"
        )
    )


# Contact type constants
CONTACT_TYPE_REPEATER = 2


class ContactAdvertPath(BaseModel):
    """A unique advert path observed for a contact."""

    path: str = Field(description="Hex-encoded routing path (empty string for direct)")
    path_len: int = Field(description="Number of hops in the path")
    next_hop: str | None = Field(
        default=None,
        description="First hop toward us as a full hop identifier, or null for direct",
    )
    first_seen: int = Field(description="Unix timestamp of first observation")
    last_seen: int = Field(description="Unix timestamp of most recent observation")
    heard_count: int = Field(description="Number of times this unique path was heard")


class ContactAdvertPathSummary(BaseModel):
    """Recent unique advertisement paths for a single contact."""

    public_key: str = Field(description="Contact public key (64-char hex)")
    paths: list[ContactAdvertPath] = Field(
        default_factory=list, description="Most recent unique advert paths"
    )


class ContactNameHistory(BaseModel):
    """A historical name used by a contact."""

    name: str
    first_seen: int
    last_seen: int


class ContactActiveRoom(BaseModel):
    """A channel/room where a contact has been active."""

    channel_key: str
    channel_name: str
    message_count: int


class NearestRepeater(BaseModel):
    """A repeater that has relayed a contact's advertisements."""

    public_key: str
    name: str | None = None
    path_len: int
    last_seen: int
    heard_count: int


class ContactDetail(BaseModel):
    """Comprehensive contact profile data."""

    contact: Contact
    name_history: list[ContactNameHistory] = Field(default_factory=list)
    dm_message_count: int = 0
    channel_message_count: int = 0
    most_active_rooms: list[ContactActiveRoom] = Field(default_factory=list)
    advert_paths: list[ContactAdvertPath] = Field(default_factory=list)
    advert_frequency: float | None = Field(
        default=None,
        description="Advert observations per hour (includes multi-path arrivals of same advert)",
    )
    nearest_repeaters: list[NearestRepeater] = Field(default_factory=list)


class NameOnlyContactDetail(BaseModel):
    """Channel activity summary for a sender name that is not tied to a known key."""

    name: str
    channel_message_count: int = 0
    most_active_rooms: list[ContactActiveRoom] = Field(default_factory=list)


class ContactAnalyticsHourlyBucket(BaseModel):
    """A single hourly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the hour bucket")
    last_24h_count: int = 0
    last_week_average: float = 0
    all_time_average: float = 0


class ContactAnalyticsWeeklyBucket(BaseModel):
    """A single weekly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the 7-day bucket")
    message_count: int = 0


class ContactAnalytics(BaseModel):
    """Unified contact analytics payload for keyed and name-only lookups."""

    lookup_type: Literal["contact", "name"]
    name: str
    contact: Contact | None = None
    name_first_seen_at: int | None = None
    name_history: list[ContactNameHistory] = Field(default_factory=list)
    dm_message_count: int = 0
    channel_message_count: int = 0
    includes_direct_messages: bool = False
    most_active_rooms: list[ContactActiveRoom] = Field(default_factory=list)
    advert_paths: list[ContactAdvertPath] = Field(default_factory=list)
    advert_frequency: float | None = Field(
        default=None,
        description="Advert observations per hour (includes multi-path arrivals of same advert)",
    )
    nearest_repeaters: list[NearestRepeater] = Field(default_factory=list)
    hourly_activity: list[ContactAnalyticsHourlyBucket] = Field(default_factory=list)
    weekly_activity: list[ContactAnalyticsWeeklyBucket] = Field(default_factory=list)


class Channel(BaseModel):
    key: str = Field(description="Channel key (32-char hex)")
    name: str
    is_hashtag: bool = False
    on_radio: bool = False
    flood_scope_override: str | None = Field(
        default=None,
        description="Per-channel outbound flood scope override (null = use global app setting)",
    )
    last_read_at: int | None = None  # Server-side read state tracking


class ChannelMessageCounts(BaseModel):
    """Time-windowed message counts for a channel."""

    last_1h: int = 0
    last_24h: int = 0
    last_48h: int = 0
    last_7d: int = 0
    all_time: int = 0


class ChannelTopSender(BaseModel):
    """A top sender in a channel over the last 24 hours."""

    sender_name: str
    sender_key: str | None = None
    message_count: int


class ChannelDetail(BaseModel):
    """Comprehensive channel profile data."""

    channel: Channel
    message_counts: ChannelMessageCounts = Field(default_factory=ChannelMessageCounts)
    first_message_at: int | None = None
    unique_sender_count: int = 0
    top_senders_24h: list[ChannelTopSender] = Field(default_factory=list)


class MessagePath(BaseModel):
    """A single path that a message took to reach us."""

    path: str = Field(description="Hex-encoded routing path")
    received_at: int = Field(description="Unix timestamp when this path was received")
    path_len: int | None = Field(
        default=None,
        description="Hop count. None = legacy (infer as len(path)//2, i.e. 1-byte hops)",
    )


class Message(BaseModel):
    id: int
    type: str = Field(description="PRIV or CHAN")
    conversation_key: str = Field(description="User pubkey for PRIV, channel key for CHAN")
    text: str
    sender_timestamp: int | None = None
    received_at: int
    paths: list[MessagePath] | None = Field(
        default=None, description="List of routing paths this message arrived via"
    )
    txt_type: int = 0
    signature: str | None = None
    sender_key: str | None = None
    outgoing: bool = False
    acked: int = 0
    sender_name: str | None = None
    channel_name: str | None = None


class MessagesAroundResponse(BaseModel):
    messages: list[Message]
    has_older: bool
    has_newer: bool


class RawPacketDecryptedInfo(BaseModel):
    """Decryption info for a raw packet (when successfully decrypted)."""

    channel_name: str | None = None
    sender: str | None = None
    channel_key: str | None = None
    contact_key: str | None = None


class RawPacketBroadcast(BaseModel):
    """Raw packet payload broadcast via WebSocket.

    This extends the database model with runtime-computed fields
    like payload_type, snr, rssi, and decryption info.
    """

    id: int
    observation_id: int = Field(
        description=(
            "Monotonic per-process ID for this RF observation (distinct from the DB packet row ID)"
        )
    )
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g., GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB")
    rssi: int | None = Field(default=None, description="Received signal strength in dBm")
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class SendDirectMessageRequest(SendMessageRequest):
    destination: str = Field(
        description="Recipient public key (64-char hex preferred; prefix must resolve uniquely)"
    )


class SendChannelMessageRequest(SendMessageRequest):
    channel_key: str = Field(description="Channel key (32-char hex)")


class RepeaterLoginRequest(BaseModel):
    """Request to log in to a repeater."""

    password: str = Field(
        default="", description="Repeater password (empty string for guest login)"
    )


class RepeaterLoginResponse(BaseModel):
    """Response from repeater login."""

    status: str = Field(description="Login result status")


class RepeaterStatusResponse(BaseModel):
    """Status telemetry from a repeater (single attempt, no retries)."""

    battery_volts: float = Field(description="Battery voltage in volts")
    tx_queue_len: int = Field(description="Transmit queue length")
    noise_floor_dbm: int = Field(description="Noise floor in dBm")
    last_rssi_dbm: int = Field(description="Last RSSI in dBm")
    last_snr_db: float = Field(description="Last SNR in dB")
    packets_received: int = Field(description="Total packets received")
    packets_sent: int = Field(description="Total packets sent")
    airtime_seconds: int = Field(description="TX airtime in seconds")
    rx_airtime_seconds: int = Field(description="RX airtime in seconds")
    uptime_seconds: int = Field(description="Uptime in seconds")
    sent_flood: int = Field(description="Flood packets sent")
    sent_direct: int = Field(description="Direct packets sent")
    recv_flood: int = Field(description="Flood packets received")
    recv_direct: int = Field(description="Direct packets received")
    flood_dups: int = Field(description="Duplicate flood packets")
    direct_dups: int = Field(description="Duplicate direct packets")
    full_events: int = Field(description="Full event queue count")


class RepeaterNodeInfoResponse(BaseModel):
    """Identity/location info from a repeater (small CLI batch)."""

    name: str | None = Field(default=None, description="Repeater name")
    lat: str | None = Field(default=None, description="Latitude")
    lon: str | None = Field(default=None, description="Longitude")
    clock_utc: str | None = Field(default=None, description="Repeater clock in UTC")


class RepeaterRadioSettingsResponse(BaseModel):
    """Radio settings from a repeater (radio/config CLI batch)."""

    firmware_version: str | None = Field(default=None, description="Firmware version string")
    radio: str | None = Field(default=None, description="Radio settings (freq,bw,sf,cr)")
    tx_power: str | None = Field(default=None, description="TX power in dBm")
    airtime_factor: str | None = Field(default=None, description="Airtime factor")
    repeat_enabled: str | None = Field(default=None, description="Repeat mode enabled")
    flood_max: str | None = Field(default=None, description="Max flood hops")


class RepeaterAdvertIntervalsResponse(BaseModel):
    """Advertisement intervals from a repeater."""

    advert_interval: str | None = Field(default=None, description="Local advert interval")
    flood_advert_interval: str | None = Field(default=None, description="Flood advert interval")


class RepeaterOwnerInfoResponse(BaseModel):
    """Owner info and guest password from a repeater."""

    owner_info: str | None = Field(default=None, description="Owner info string")
    guest_password: str | None = Field(default=None, description="Guest password")


class LppSensor(BaseModel):
    """A single CayenneLPP sensor reading from req_telemetry_sync."""

    channel: int = Field(description="LPP channel number")
    type_name: str = Field(description="Sensor type name (e.g. temperature, humidity)")
    value: float | dict = Field(
        description="Scalar value or dict for multi-value sensors (GPS, accel)"
    )


class RepeaterLppTelemetryResponse(BaseModel):
    """CayenneLPP sensor telemetry from a repeater."""

    sensors: list[LppSensor] = Field(default_factory=list, description="List of sensor readings")


class NeighborInfo(BaseModel):
    """Information about a neighbor seen by a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (4-12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    snr: float = Field(description="Signal-to-noise ratio in dB")
    last_heard_seconds: int = Field(description="Seconds since last heard")


class AclEntry(BaseModel):
    """Access control list entry for a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    permission: int = Field(
        description="Permission level: 0=Guest, 1=Read-only, 2=Read-write, 3=Admin"
    )
    permission_name: str = Field(description="Human-readable permission name")


class RepeaterNeighborsResponse(BaseModel):
    """Neighbors list from a repeater."""

    neighbors: list[NeighborInfo] = Field(
        default_factory=list, description="List of neighbors seen by repeater"
    )


class RepeaterAclResponse(BaseModel):
    """ACL list from a repeater."""

    acl: list[AclEntry] = Field(default_factory=list, description="Access control list")


class TraceResponse(BaseModel):
    """Result of a direct (zero-hop) trace to a contact."""

    remote_snr: float | None = Field(
        default=None, description="SNR at which the target heard us (dB)"
    )
    local_snr: float | None = Field(
        default=None, description="SNR at which we heard the target on the bounce-back (dB)"
    )
    path_len: int = Field(description="Number of hops in the trace path")


class CommandRequest(BaseModel):
    """Request to send a CLI command to a repeater."""

    command: str = Field(min_length=1, description="CLI command to send")


class CommandResponse(BaseModel):
    """Response from a repeater CLI command."""

    command: str = Field(description="The command that was sent")
    response: str = Field(description="Response from the repeater")
    sender_timestamp: int | None = Field(
        default=None, description="Timestamp from the repeater's response"
    )


class Favorite(BaseModel):
    """A favorite conversation."""

    type: Literal["channel", "contact"] = Field(description="'channel' or 'contact'")
    id: str = Field(description="Channel key or contact public key")


class UnreadCounts(BaseModel):
    """Aggregated unread counts, mention flags, and last message times for all conversations."""

    counts: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> unread count"
    )
    mentions: dict[str, bool] = Field(
        default_factory=dict, description="Map of stateKey -> has mention"
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> last message timestamp"
    )
    last_read_ats: dict[str, int | None] = Field(
        default_factory=dict, description="Map of stateKey -> server-side last_read_at boundary"
    )


class AppSettings(BaseModel):
    """Application settings stored in the database."""

    max_radio_contacts: int = Field(
        default=200,
        description=(
            "Configured radio contact capacity used for maintenance thresholds; "
            "favorites reload first, then background fill targets about 80% of this value"
        ),
    )
    favorites: list[Favorite] = Field(
        default_factory=list, description="List of favorited conversations"
    )
    auto_decrypt_dm_on_advert: bool = Field(
        default=False,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    sidebar_sort_order: Literal["recent", "alpha"] = Field(
        default="recent",
        description="Sidebar sort order: 'recent' or 'alpha'",
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict,
        description="Map of conversation state keys to last message timestamps",
    )
    preferences_migrated: bool = Field(
        default=False,
        description="Whether preferences have been migrated from localStorage",
    )
    advert_interval: int = Field(
        default=0,
        description="Periodic advertisement interval in seconds (0 = disabled)",
    )
    last_advert_time: int = Field(
        default=0,
        description="Unix timestamp of last advertisement sent (0 = never)",
    )
    flood_scope: str = Field(
        default="",
        description="Outbound flood scope / region name (empty = disabled, no tagging)",
    )
    blocked_keys: list[str] = Field(
        default_factory=list,
        description="Public keys whose messages are hidden from the UI",
    )
    blocked_names: list[str] = Field(
        default_factory=list,
        description="Display names whose messages are hidden from the UI",
    )


class FanoutConfig(BaseModel):
    """Configuration for a single fanout integration."""

    id: str
    type: str  # 'mqtt_private' | 'mqtt_community' | 'bot' | 'webhook' | 'apprise' | 'sqs'
    name: str
    enabled: bool
    config: dict
    scope: dict
    sort_order: int = 0
    created_at: int = 0


class BusyChannel(BaseModel):
    channel_key: str
    channel_name: str
    message_count: int


class ContactActivityCounts(BaseModel):
    last_hour: int
    last_24_hours: int
    last_week: int


class PathHashWidthStats(BaseModel):
    total_packets: int
    single_byte: int
    double_byte: int
    triple_byte: int
    single_byte_pct: float
    double_byte_pct: float
    triple_byte_pct: float


class StatisticsResponse(BaseModel):
    busiest_channels_24h: list[BusyChannel]
    contact_count: int
    repeater_count: int
    channel_count: int
    total_packets: int
    decrypted_packets: int
    undecrypted_packets: int
    total_dms: int
    total_channel_messages: int
    total_outgoing: int
    contacts_heard: ContactActivityCounts
    repeaters_heard: ContactActivityCounts
    path_hash_width_24h: PathHashWidthStats
