# Backend AGENTS.md

This document provides context for AI assistants and developers working on the FastAPI backend.

## Technology Stack

- **FastAPI** - Async web framework with automatic OpenAPI docs
- **aiosqlite** - Async SQLite driver
- **meshcore** - MeshCore radio library (local dependency at `../meshcore_py`)
- **Pydantic** - Data validation and settings management
- **PyCryptodome** - AES-128 encryption for packet decryption
- **UV** - Python package manager

## Directory Structure

```
app/
├── main.py           # FastAPI app, lifespan, router registration, static file serving
├── config.py         # Pydantic settings (env vars: MESHCORE_*)
├── database.py       # SQLite schema, connection management, runs migrations
├── migrations.py     # Database migrations using SQLite user_version pragma
├── models.py         # Pydantic models for API request/response
├── repository.py     # Database CRUD (ContactRepository, ChannelRepository, etc.)
├── radio.py          # RadioManager - serial connection to MeshCore device
├── radio_sync.py     # Periodic sync, contact auto-loading to radio
├── decoder.py        # Packet decryption (channel + direct messages)
├── packet_processor.py # Raw packet processing, advertisement handling
├── keystore.py       # Ephemeral key store (private key in memory only)
├── event_handlers.py # Radio event subscriptions, ACK tracking, repeat detection
├── websocket.py      # WebSocketManager for real-time client updates
└── routers/          # All routes prefixed with /api
    ├── health.py     # GET /api/health
    ├── radio.py      # Radio config, advertise, private key, reboot
    ├── contacts.py   # Contact CRUD, radio sync, mark-read
    ├── channels.py   # Channel CRUD, radio sync, mark-read
    ├── messages.py   # Message list and send (direct/channel)
    ├── packets.py    # Raw packet endpoints, historical decryption
    ├── read_state.py # Bulk read state operations (mark-all-read)
    ├── settings.py   # App settings (max_radio_contacts)
    └── ws.py         # WebSocket endpoint at /api/ws
```

## Key Architectural Patterns

### Repository Pattern

All database operations go through repository classes in `repository.py`:

```python
from app.repository import ContactRepository, ChannelRepository, MessageRepository, RawPacketRepository, AppSettingsRepository

# Examples
contact = await ContactRepository.get_by_key_prefix("abc123")
await MessageRepository.create(msg_type="PRIV", text="Hello", received_at=timestamp)
await RawPacketRepository.mark_decrypted(packet_id, message_id)

# App settings (single-row pattern)
settings = await AppSettingsRepository.get()
await AppSettingsRepository.update(auto_decrypt_dm_on_advert=True)
await AppSettingsRepository.add_favorite("contact", public_key)
await AppSettingsRepository.update_last_message_time("channel-KEY", timestamp)
```

### Radio Connection

`RadioManager` in `radio.py` handles serial connection:

```python
from app.radio import radio_manager

# Access meshcore instance
if radio_manager.meshcore:
    await radio_manager.meshcore.commands.send_msg(dst, msg)
```

Auto-detection scans common serial ports when `MESHCORE_SERIAL_PORT` is not set.

### Event-Driven Architecture

Radio events flow through `event_handlers.py`:

| Event | Handler | Actions |
|-------|---------|---------|
| `CONTACT_MSG_RECV` | `on_contact_message` | Store message, update contact last_seen, broadcast via WS |
| `CHANNEL_MSG_RECV` | `on_channel_message` | Store message, broadcast via WS |
| `RAW_DATA` | `on_raw_data` | Store packet, try decrypt with all channel keys, detect repeats |
| `ADVERTISEMENT` | `on_advertisement` | Upsert contact with location |
| `ACK` | `on_ack` | Match pending ACKs, mark message acked, broadcast |

### WebSocket Broadcasting

Real-time updates use `ws_manager` singleton:

```python
from app.websocket import ws_manager

# Broadcast to all connected clients
await ws_manager.broadcast("message", {"id": 1, "text": "Hello"})
```

Event types: `health`, `contacts`, `channels`, `message`, `contact`, `raw_packet`, `message_acked`, `error`

Helper functions for common broadcasts:

```python
from app.websocket import broadcast_error, broadcast_health

# Notify clients of errors (shows toast in frontend)
broadcast_error("Operation failed", "Additional details")

# Notify clients of connection status change
broadcast_health(radio_connected=True, serial_port="/dev/ttyUSB0")
```

### Connection Monitoring

`RadioManager` includes a background task that monitors connection status:

- Checks connection every 5 seconds
- Broadcasts `health` event on status change
- Attempts automatic reconnection when connection lost
- **Re-registers event handlers after successful auto-reconnect** (critical for message delivery)
- Resilient to transient errors (logs and continues rather than crashing)
- Supports manual reconnection via `POST /api/radio/reconnect`

```python
from app.radio import radio_manager

# Manual reconnection
success = await radio_manager.reconnect()

# Background monitor (started automatically in app lifespan)
await radio_manager.start_connection_monitor()
await radio_manager.stop_connection_monitor()
```

### Message Polling

Periodic message polling serves as a fallback for platforms where push events are unreliable.
Use `pause_polling()` to temporarily suspend polling during operations that need exclusive
radio access (e.g., repeater CLI commands):

```python
from app.radio_sync import pause_polling, is_polling_paused

# Pause polling during sensitive operations (supports nesting)
async with pause_polling():
    # Polling is paused here
    await do_repeater_operation()

    async with pause_polling():
        # Still paused (nested)
        await do_another_operation()

    # Still paused (outer context active)

# Polling resumes when all contexts exit

# Check current state
if is_polling_paused():
    print("Polling is currently paused")
```

## Database Schema

```sql
contacts (
    public_key TEXT PRIMARY KEY,  -- 64-char hex
    name TEXT,
    type INTEGER DEFAULT 0,       -- 0=unknown, 1=client, 2=repeater, 3=room
    flags INTEGER DEFAULT 0,
    last_path TEXT,               -- Routing path hex
    last_path_len INTEGER DEFAULT -1,
    last_advert INTEGER,          -- Unix timestamp of last advertisement
    lat REAL, lon REAL,
    last_seen INTEGER,
    on_radio INTEGER DEFAULT 0,   -- Boolean: contact loaded on radio
    last_contacted INTEGER,       -- Unix timestamp of last message sent/received
    last_read_at INTEGER          -- Unix timestamp when conversation was last read
)

channels (
    key TEXT PRIMARY KEY,         -- 32-char hex channel key
    name TEXT NOT NULL,
    is_hashtag INTEGER DEFAULT 0, -- Key derived from SHA256(name)[:16]
    on_radio INTEGER DEFAULT 0,
    last_read_at INTEGER          -- Unix timestamp when channel was last read
)

messages (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,           -- 'PRIV' or 'CHAN'
    conversation_key TEXT NOT NULL, -- User pubkey for PRIV, channel key for CHAN
    text TEXT NOT NULL,
    sender_timestamp INTEGER,
    received_at INTEGER NOT NULL,
    path TEXT,                    -- Hex-encoded routing path (2 chars per hop), null for outgoing
    txt_type INTEGER DEFAULT 0,
    signature TEXT,
    outgoing INTEGER DEFAULT 0,
    acked INTEGER DEFAULT 0,
    UNIQUE(type, conversation_key, text, sender_timestamp)  -- Deduplication
)

raw_packets (
    id INTEGER PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    data BLOB NOT NULL,           -- Raw packet bytes
    decrypted INTEGER DEFAULT 0,
    message_id INTEGER,           -- FK to messages if decrypted
    decrypt_attempts INTEGER DEFAULT 0,
    last_attempt INTEGER,
    FOREIGN KEY (message_id) REFERENCES messages(id)
)

app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single-row pattern
    max_radio_contacts INTEGER DEFAULT 200,
    favorites TEXT DEFAULT '[]',            -- JSON array of {type, id}
    auto_decrypt_dm_on_advert INTEGER DEFAULT 0,
    sidebar_sort_order TEXT DEFAULT 'recent',  -- 'recent' or 'alpha'
    last_message_times TEXT DEFAULT '{}',   -- JSON object of state_key -> timestamp
    preferences_migrated INTEGER DEFAULT 0  -- One-time migration flag
)
```

## Database Migrations (`migrations.py`)

Schema migrations use SQLite's `user_version` pragma for version tracking:

```python
from app.migrations import get_version, set_version, run_migrations

# Check current schema version
version = await get_version(conn)  # Returns int (0 for new/unmigrated DB)

# Run pending migrations (called automatically on startup)
applied = await run_migrations(conn)  # Returns number of migrations applied
```

### How It Works

1. `database.py` calls `run_migrations()` after schema initialization
2. Each migration checks `user_version` and runs if needed
3. Migrations are idempotent (safe to run multiple times)
4. `ALTER TABLE ADD COLUMN` handles existing columns gracefully

### Adding a New Migration

```python
# In migrations.py
async def run_migrations(conn: aiosqlite.Connection) -> int:
    version = await get_version(conn)
    applied = 0

    if version < 1:
        await _migrate_001_add_last_read_at(conn)
        await set_version(conn, 1)
        applied += 1

    # Add new migrations here:
    # if version < 2:
    #     await _migrate_002_something(conn)
    #     await set_version(conn, 2)
    #     applied += 1

    return applied
```

## Packet Decryption (`decoder.py`)

The decoder handles MeshCore packet decryption for historical packet analysis:

### Packet Types

```python
class PayloadType(IntEnum):
    GROUP_TEXT = 0x05      # Channel messages (decryptable)
    TEXT_MESSAGE = 0x02   # Direct messages
    ACK = 0x03
    ADVERT = 0x04
    # ... see decoder.py for full list
```

### Channel Key Derivation

Hashtag channels derive keys from name:
```python
channel_key = hashlib.sha256(b"#channelname").digest()[:16]
```

### Decryption Flow

1. Parse packet header to get payload type
2. For `GROUP_TEXT`: extract channel_hash (1 byte), cipher_mac (2 bytes), ciphertext
3. Verify HMAC-SHA256 using 32-byte secret (key + 16 zero bytes)
4. Decrypt with AES-128 ECB
5. Parse decrypted content: timestamp (4 bytes), flags (1 byte), "sender: message" text

```python
from app.decoder import try_decrypt_packet_with_channel_key

result = try_decrypt_packet_with_channel_key(raw_bytes, channel_key)
if result:
    print(f"{result.sender}: {result.message}")
```

### Direct Message Decryption

Direct messages use ECDH key exchange (Ed25519 → X25519) for shared secret derivation.

**Key storage**: The private key is exported from the radio on startup and stored in memory
via `keystore.py`. This enables server-side DM decryption even when contacts aren't loaded
on the radio.

**Real-time decryption**: When a `RAW_DATA` event contains a `TEXT_MESSAGE` packet, the
`packet_processor.py` attempts to decrypt it using known contact public keys and the
stored private key.

**Historical decryption**: When creating a contact with `try_historical=True`, the server
attempts to decrypt all stored `TEXT_MESSAGE` packets for that contact.

**Direction detection**: The decoder uses the 1-byte dest_hash and src_hash to determine
if a message is incoming or outgoing. Edge case: when both bytes match (1/256 chance),
defaults to treating as incoming.

```python
from app.decoder import try_decrypt_dm

result = try_decrypt_dm(raw_bytes, private_key, contact_public_key)
if result:
    print(f"{result.message} (timestamp={result.timestamp})")
```

## Advertisement Parsing (`decoder.py`)

Advertisement packets contain contact information including optional GPS coordinates.

### Packet Structure

```
Bytes 0-31:   Public key (32 bytes)
Bytes 32-35:  Timestamp (4 bytes, little-endian Unix timestamp)
Bytes 36-99:  Signature (64 bytes)
Byte 100:     App flags
Bytes 101+:   Optional fields (location, name) based on flags
```

### App Flags (byte 100)

- Bits 0-3: Device role (1=Chat, 2=Repeater, 3=Room, 4=Sensor)
- Bit 4: Has location (lat/lon follow)
- Bit 5: Has feature 1
- Bit 6: Has feature 2
- Bit 7: Has name (null-terminated string at end)

### GPS Extraction

When bit 4 is set, latitude and longitude follow as signed int32 little-endian values,
divided by 1,000,000 to get decimal degrees:

```python
from app.decoder import parse_advertisement

advert = parse_advertisement(payload_bytes)
if advert:
    print(f"Device role: {advert.device_role}")  # 1=Chat, 2=Repeater
    if advert.lat and advert.lon:
        print(f"Location: {advert.lat}, {advert.lon}")
```

### Data Flow

1. `event_handlers.py` receives ADVERTISEMENT event
2. `packet_processor.py` calls `parse_advertisement()` to extract data
3. Contact is upserted with location data (`lat`, `lon`) and `device_role` as `type`
4. Frontend MapView displays contacts with GPS coordinates

## ACK and Repeat Detection

The `acked` field is an integer count, not a boolean:
- `0` = not acked
- `1` = one ACK/echo received
- `2+` = multiple flood echoes received

### Direct Message ACKs

When sending a direct message, an expected ACK code is tracked:
```python
from app.event_handlers import track_pending_ack

track_pending_ack(expected_ack="abc123", message_id=42, timeout_ms=30000)
```

When ACK event arrives, the message's ack count is incremented.

### Channel Message Repeats

Flood messages echo back through repeaters. Detection uses:
- Channel key
- Text hash
- Timestamp (±5 second window)

Each repeat increments the ack count. The frontend displays:
- `?` = no acks
- `✓` = 1 echo
- `✓2`, `✓3`, etc. = multiple echoes (real-time updates via WebSocket)

### Auto-Contact Sync to Radio

To enable the radio to auto-ACK incoming DMs, recent non-repeater contacts are
automatically loaded to the radio. Configured via `max_radio_contacts` setting (default 200).

- Triggered on each advertisement from a non-repeater contact
- Loads most recently contacted non-repeaters (by `last_contacted` timestamp)
- Throttled to at most once per 30 seconds
- `last_contacted` updated on message send/receive

```python
from app.radio_sync import sync_recent_contacts_to_radio

result = await sync_recent_contacts_to_radio(force=True)
# Returns: {"loaded": 5, "already_on_radio": 195, "failed": 0}
```

## API Endpoints

All endpoints are prefixed with `/api`.

### Health
- `GET /api/health` - Connection status, serial port

### Radio
- `GET /api/radio/config` - Read config (public key, name, radio params)
- `PATCH /api/radio/config` - Update name, lat/lon, tx_power, radio params
- `PUT /api/radio/private-key` - Import private key to radio (write-only)
- `POST /api/radio/advertise?flood=true` - Send advertisement
- `POST /api/radio/reboot` - Reboot radio or reconnect if disconnected
- `POST /api/radio/reconnect` - Manual reconnection attempt

### Contacts
- `GET /api/contacts` - List from database
- `GET /api/contacts/{key}` - Get by public key or prefix
- `POST /api/contacts` - Create contact (optionally trigger historical DM decryption)
- `POST /api/contacts/sync` - Pull from radio to database
- `POST /api/contacts/{key}/add-to-radio` - Push to radio
- `POST /api/contacts/{key}/remove-from-radio` - Remove from radio
- `POST /api/contacts/{key}/mark-read` - Mark conversation as read (updates last_read_at)
- `POST /api/contacts/{key}/telemetry` - Request telemetry from repeater (see below)

### Channels
- `GET /api/channels` - List from database
- `GET /api/channels/{key}` - Get by channel key
- `POST /api/channels` - Create (hashtag if name starts with # or no key provided)
- `POST /api/channels/sync` - Pull from radio
- `POST /api/channels/{key}/mark-read` - Mark channel as read (updates last_read_at)
- `DELETE /api/channels/{key}` - Delete channel

### Read State
- `POST /api/read-state/mark-all-read` - Mark all contacts and channels as read

### Messages
- `GET /api/messages?type=&conversation_key=&limit=&offset=` - List with filters
- `POST /api/messages/direct` - Send direct message
- `POST /api/messages/channel` - Send channel message

### Packets
- `GET /api/packets/undecrypted/count` - Count of undecrypted packets
- `POST /api/packets/decrypt/historical` - Try decrypting old packets with new key

### Settings
- `GET /api/settings` - Get all app settings
- `PATCH /api/settings` - Update settings (max_radio_contacts, auto_decrypt_dm_on_advert, sidebar_sort_order)
- `POST /api/settings/favorites` - Add a favorite
- `DELETE /api/settings/favorites` - Remove a favorite
- `POST /api/settings/favorites/toggle` - Toggle favorite status
- `POST /api/settings/last-message-time` - Update last message time for a conversation
- `POST /api/settings/migrate` - One-time migration from frontend localStorage

### WebSocket
- `WS /api/ws` - Real-time updates (health, contacts, channels, messages, raw packets)

### Static Files (Production)
In production, the backend also serves the frontend:
- `/` - Serves `frontend/dist/index.html`
- `/assets/*` - Serves compiled JS/CSS from `frontend/dist/assets/`
- `/*` - Falls back to `index.html` for SPA routing

## Testing

Run tests with:
```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Key test files:
- `tests/test_decoder.py` - Channel + direct message decryption, key exchange, real-world test vectors
- `tests/test_keystore.py` - Ephemeral key store operations
- `tests/test_event_handlers.py` - ACK tracking, repeat detection, CLI response filtering
- `tests/test_api.py` - API endpoint tests, read state tracking
- `tests/test_migrations.py` - Migration system, schema versioning

## Common Tasks

### Adding a New Endpoint

1. Create or update router in `app/routers/`
2. Define Pydantic models in `app/models.py` if needed
3. Add repository methods in `app/repository.py` for database operations
4. Register router in `app/main.py` if new file
5. Add tests in `tests/`

### Adding a New Event Handler

1. Define handler in `app/event_handlers.py`
2. Register in `register_event_handlers()` function
3. Broadcast updates via `ws_manager` as needed

### Working with Radio Commands

```python
# Available via radio_manager.meshcore.commands
await mc.commands.send_msg(dst, msg)
await mc.commands.send_chan_msg(chan, msg)
await mc.commands.get_contacts()
await mc.commands.add_contact(contact_dict)
await mc.commands.set_channel(idx, name, key)
await mc.commands.send_advert(flood=True)
```

## Repeater Telemetry

The `POST /api/contacts/{key}/telemetry` endpoint fetches status, neighbors, and ACL from repeaters (contact type=2).

### Request Flow

1. Verify contact exists and is a repeater (type=2)
2. Add contact to radio with stored path data (from advertisements)
3. Send login with password
4. Request status with retries (3 attempts, 10s timeout)
5. Fetch neighbors with `fetch_all_neighbours()` (handles pagination)
6. Fetch ACL with `req_acl_sync()`
7. Resolve pubkey prefixes to contact names from database

### ACL Permission Levels

```python
ACL_PERMISSION_NAMES = {
    0: "Guest",
    1: "Read-only",
    2: "Read-write",
    3: "Admin",
}
```

### Response Models

```python
class NeighborInfo(BaseModel):
    pubkey_prefix: str      # 4-12 char prefix
    name: str | None        # Resolved contact name
    snr: float              # Signal-to-noise ratio in dB
    last_heard_seconds: int # Seconds since last heard

class AclEntry(BaseModel):
    pubkey_prefix: str      # 12 char prefix
    name: str | None        # Resolved contact name
    permission: int         # 0-3
    permission_name: str    # Human-readable name

class TelemetryResponse(BaseModel):
    # Status fields
    pubkey_prefix: str
    battery_volts: float    # Converted from mV
    uptime_seconds: int
    # ... signal quality, packet counts, etc.

    # Related data
    neighbors: list[NeighborInfo]
    acl: list[AclEntry]
```

## Repeater CLI Commands

After login via telemetry endpoint, you can send CLI commands to repeaters:

### Endpoint

`POST /api/contacts/{key}/command` - Send a CLI command (assumes already logged in)

### Request/Response

```python
class CommandRequest(BaseModel):
    command: str  # CLI command to send

class CommandResponse(BaseModel):
    command: str           # Echo of sent command
    response: str          # Response from repeater
    sender_timestamp: int | None  # Timestamp from response
```

### Common Commands

```
get name / set name <value>     # Repeater name
get tx / set tx <dbm>           # TX power
get radio / set radio <freq,bw,sf,cr>  # Radio params
tempradio <freq,bw,sf,cr,mins>  # Temporary radio change
setperm <pubkey> <0-3>          # ACL: 0=guest, 1=ro, 2=rw, 3=admin
clock / clock sync              # Get/sync time
ver                             # Firmware version
reboot                          # Restart repeater
```

### CLI Response Filtering

CLI responses have `txt_type=1` (vs `txt_type=0` for normal messages). The event handler
in `event_handlers.py` skips these to prevent duplicates—the command endpoint returns
the response directly, so we don't also store/broadcast via WebSocket.

```python
# In on_contact_message()
txt_type = payload.get("txt_type", 0)
if txt_type == 1:
    return  # Skip CLI responses
```

### Helper Function

`prepare_repeater_connection()` handles the login dance:
1. Add contact to radio with stored path from DB (`out_path`, `out_path_len`)
2. Send login with password
3. Wait for key exchange to complete

### Contact Path Tracking

When advertisements are received, path data is extracted and stored:
- `last_path`: Hex string of routing path bytes
- `last_path_len`: Number of hops (-1=flood/unknown, 0=direct, >0=hops through repeaters)

**Shortest path selection**: When receiving echoed advertisements within 60 seconds, the shortest path is kept. This ensures we use the most efficient route when multiple paths exist.
