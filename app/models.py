from typing import Literal

from pydantic import BaseModel, Field


class Contact(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0  # 0=unknown, 1=client, 2=repeater, 3=room
    flags: int = 0
    last_path: str | None = None
    last_path_len: int = -1
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool = False
    last_contacted: int | None = None  # Last time we sent/received a message
    last_read_at: int | None = None  # Server-side read state tracking

    def to_radio_dict(self) -> dict:
        """Convert to the dict format expected by meshcore radio commands.

        The radio API uses different field names (adv_name, out_path, etc.)
        than our database schema (name, last_path, etc.).
        """
        return {
            "public_key": self.public_key,
            "adv_name": self.name or "",
            "type": self.type,
            "flags": self.flags,
            "out_path": self.last_path or "",
            "out_path_len": self.last_path_len,
            "adv_lat": self.lat or 0.0,
            "adv_lon": self.lon or 0.0,
            "last_advert": self.last_advert or 0,
        }

    @staticmethod
    def from_radio_dict(public_key: str, radio_data: dict, on_radio: bool = False) -> dict:
        """Convert radio contact data to database format dict.

        This is the inverse of to_radio_dict(), used when syncing contacts
        from radio to database.
        """
        return {
            "public_key": public_key,
            "name": radio_data.get("adv_name"),
            "type": radio_data.get("type", 0),
            "flags": radio_data.get("flags", 0),
            "last_path": radio_data.get("out_path"),
            "last_path_len": radio_data.get("out_path_len", -1),
            "lat": radio_data.get("adv_lat"),
            "lon": radio_data.get("adv_lon"),
            "last_advert": radio_data.get("last_advert"),
            "on_radio": on_radio,
        }


class CreateContactRequest(BaseModel):
    """Request to create a new contact."""

    public_key: str = Field(min_length=64, max_length=64, description="Public key (64-char hex)")
    name: str | None = Field(default=None, description="Display name for the contact")
    try_historical: bool = Field(
        default=False,
        description="Attempt to decrypt historical DM packets for this contact",
    )


# Contact type constants
CONTACT_TYPE_REPEATER = 2


class Channel(BaseModel):
    key: str = Field(description="Channel key (32-char hex)")
    name: str
    is_hashtag: bool = False
    on_radio: bool = False
    last_read_at: int | None = None  # Server-side read state tracking


class MessagePath(BaseModel):
    """A single path that a message took to reach us."""

    path: str = Field(description="Hex-encoded routing path (2 chars per hop)")
    received_at: int = Field(description="Unix timestamp when this path was received")


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
    outgoing: bool = False
    acked: int = 0


class RawPacket(BaseModel):
    """Raw packet as stored in the database."""

    id: int
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    message_id: int | None = None

    @property
    def decrypted(self) -> bool:
        """A packet is decrypted iff it has a linked message_id."""
        return self.message_id is not None


class RawPacketDecryptedInfo(BaseModel):
    """Decryption info for a raw packet (when successfully decrypted)."""

    channel_name: str | None = None
    sender: str | None = None


class RawPacketBroadcast(BaseModel):
    """Raw packet payload broadcast via WebSocket.

    This extends the database model with runtime-computed fields
    like payload_type, snr, rssi, and decryption info.
    """

    id: int
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
    destination: str = Field(description="Public key or prefix of recipient")


class SendChannelMessageRequest(SendMessageRequest):
    channel_key: str = Field(description="Channel key (32-char hex)")


class TelemetryRequest(BaseModel):
    password: str = Field(
        default="", description="Repeater password (empty string for no password)"
    )


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


class TelemetryResponse(BaseModel):
    """Telemetry data from a repeater, formatted for human readability."""

    pubkey_prefix: str = Field(description="12-char public key prefix")
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
    neighbors: list[NeighborInfo] = Field(
        default_factory=list, description="List of neighbors seen by repeater"
    )
    acl: list[AclEntry] = Field(default_factory=list, description="Access control list")


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


class AppSettings(BaseModel):
    """Application settings stored in the database."""

    max_radio_contacts: int = Field(
        default=200,
        description="Maximum non-repeater contacts to keep on radio for DM ACKs",
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
    bot_enabled: bool = Field(
        default=False,
        description="Whether the message bot is enabled",
    )
    bot_code: str = Field(
        default="",
        description="Python code for the message bot function",
    )
