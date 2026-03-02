# RemoteTerm for MeshCore

## Important Rules

**NEVER make git commits.** A human must make all commits. You may stage files and prepare commit messages, but do not run `git commit`.

If instructed to "run all tests" or "get ready for a commit" or other summative, work ending directives, run:

```bash
./scripts/all_quality.sh
```

This runs all linting, formatting, type checking, tests, and builds for both backend and frontend in parallel. All checks must pass green.

## Overview

A web interface for MeshCore mesh radio networks. The backend connects to a MeshCore-compatible radio over Serial, TCP, or BLE and exposes REST/WebSocket APIs. The React frontend provides real-time messaging and radio configuration.

**For detailed component documentation, see:**
- `app/AGENTS.md` - Backend (FastAPI, database, radio connection, packet decryption)
- `frontend/AGENTS.md` - Frontend (React, state management, WebSocket, components)
- `frontend/src/components/AGENTS.md` - Frontend visualizer feature (a particularly complex and long force-directed graph visualizer component; can skip this file unless you're working on that feature)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ StatusBar│  │ Sidebar  │  │MessageList│  │  MessageInput   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │      CrackerPanel (global collapsible, WebGPU cracking)    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                           │                                      │
│                    useWebSocket ←──── Real-time updates          │
│                           │                                      │
│                      api.ts ←──── REST API calls                 │
└───────────────────────────┼──────────────────────────────────────┘
                            │ HTTP + WebSocket (/api/*)
┌───────────────────────────┼──────────────────────────────────────┐
│                      Backend (FastAPI)                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Routers  │→ │ Repositories │→ │  SQLite DB │  │ WebSocket │  │
│  └──────────┘  └──────────────┘  └────────────┘  │  Manager  │  │
│        ↓                                          └───────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              RadioManager + Event Handlers               │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┼──────────────────────────────────────┘
                            │ Serial / TCP / BLE
                     ┌──────┴──────┐
                     │ MeshCore    │
                     │   Radio     │
                     └─────────────┘
```

## Feature Priority

**Primary (must work correctly):**
- Sending and receiving direct messages and channel messages
- Accurate message display: correct ordering, deduplication, pagination/history loading, and real-time updates without data loss or duplicates
- Accurate ACK tracking, repeat/echo counting, and path display
- Historical packet decryption (recovering incoming messages using newly-added keys)
- Outgoing DMs are stored as plaintext by the send endpoint — no decryption needed

**Secondary:**
- Channel key cracker (WebGPU brute-force)
- Repeater management (telemetry, CLI commands, ACL)

**Tertiary (best-effort, quality-of-life):**
- Raw packet feed — a debug/observation tool ("radio aquarium"); interesting to watch or copy packets from, but not critical infrastructure
- Map view — visual display of node locations from advertisements
- Network visualizer — force-directed graph of mesh topology
- Bot system — automated message responses
- Read state tracking / mark-all-read — convenience feature for unread badges; no need for transactional atomicity or race-condition hardening

## Error Handling Philosophy

**Background tasks** (WebSocket broadcasts, periodic sync, contact auto-loading, etc.) use fire-and-forget `asyncio.create_task`. Exceptions in these tasks are logged to the backend logs, which is sufficient for debugging. There is no need to track task references or add done-callbacks purely for error visibility. If there's a convenient way to bubble an error to the frontend (e.g., via `broadcast_error` for user-actionable problems), do so, but this is minor and best-effort.

## Key Design Principles

1. **Store-and-serve**: Backend stores all packets even when no client is connected
2. **Parallel storage**: Messages stored both decrypted (when possible) and as raw packets
3. **Extended capacity**: Server stores contacts/channels beyond radio limits (~350 contacts, ~40 channels)
4. **Real-time updates**: WebSocket pushes events; REST for actions; optional MQTT forwarding
5. **Offline-capable**: Radio operates independently; server syncs when connected
6. **Auto-reconnect**: Background monitor detects disconnection and attempts reconnection

## Intentional Security Design Decisions

The following are **deliberate design choices**, not bugs. They are documented in the README with appropriate warnings. Do not "fix" these or flag them as vulnerabilities.

1. **No CORS restrictions**: The backend allows all origins (`allow_origins=["*"]`). This lets users access their radio from any device/origin on their network without configuration hassle.
2. **No authentication or authorization**: There is no login, no API keys, no session management. The app is designed for trusted networks (home LAN, VPN). The README warns users not to expose it to untrusted networks.
3. **Arbitrary bot code execution**: The bot system (`app/bot.py`) executes user-provided Python via `exec()` with full `__builtins__`. This is intentional — bots are a power-user feature for automation. The README explicitly warns that anyone on the network can execute arbitrary code through this.

## Intentional Packet Handling Decision

Raw packet handling uses two identities by design:
- **`id` (DB packet row ID)**: storage identity from payload-hash deduplication (path bytes are excluded), so repeated payloads share one stored raw-packet row.
- **`observation_id` (WebSocket only)**: realtime observation identity, unique per RF arrival, so path-diverse repeats are still visible in-session.

Frontend packet-feed consumers should treat `observation_id` as the dedup/render key, while `id` remains the storage reference.

## Contact Advert Path Memory

To improve repeater disambiguation in the network visualizer, the backend stores recent unique advertisement paths per contact in a dedicated table (`contact_advert_paths`).

- This is independent of raw-packet payload deduplication.
- Paths are keyed per contact + path, with `heard_count`, `first_seen`, and `last_seen`.
- Only the N most recent unique paths are retained per contact (currently 10).
- See `frontend/src/components/AGENTS.md` § "Advert-Path Identity Hints" for how the visualizer consumes this data.

## Data Flow

### Incoming Messages

1. Radio receives raw bytes → `packet_processor.py` parses, decrypts, deduplicates, and stores in database (primary path via `RX_LOG_DATA` event)
2. `event_handlers.py` handles higher-level events (`CONTACT_MSG_RECV`, `ACK`) as a fallback/supplement
3. `broadcast_event()` in `websocket.py` fans out to both WebSocket clients and MQTT
4. Frontend `useWebSocket` receives → updates React state

### Outgoing Messages

1. User types message → clicks send
2. `api.sendChannelMessage()` → POST to backend
3. Backend calls `radio_manager.meshcore.commands.send_chan_msg()`
4. Message stored in database with `outgoing=true`
5. For direct messages: ACK tracked; for channel: repeat detection

### ACK and Repeat Detection

**Direct messages**: Expected ACK code is tracked. When ACK event arrives, message marked as acked.

**Channel messages**: Flood messages echo back through repeaters. Repeats are identified by the database UNIQUE constraint on `(type, conversation_key, text, sender_timestamp)` — when an INSERT hits a duplicate, `_handle_duplicate_message()` in `packet_processor.py` adds the new path and, for outgoing messages only, increments the ack count. Incoming repeats add path data but do not change the ack count. There is no timestamp-windowed matching; deduplication is exact-match only.

This message-layer echo/path handling is independent of raw-packet storage deduplication.

## Directory Structure

```
.
├── app/                    # FastAPI backend
│   ├── AGENTS.md           # Backend documentation
│   ├── bot.py              # Bot execution and outbound bot sends
│   ├── main.py             # App entry, lifespan
│   ├── routers/            # API endpoints
│   ├── packet_processor.py # Raw packet pipeline, dedup, path handling
│   ├── repository/         # Database CRUD (contacts, channels, messages, raw_packets, settings)
│   ├── event_handlers.py   # Radio events
│   ├── decoder.py          # Packet decryption
│   ├── websocket.py        # Real-time broadcasts
│   ├── mqtt_base.py        # Shared MQTT publisher base class (lifecycle, reconnect, backoff)
│   ├── mqtt.py             # Private MQTT publisher
│   └── community_mqtt.py   # Community MQTT publisher (raw packet sharing)
├── frontend/               # React frontend
│   ├── AGENTS.md           # Frontend documentation
│   ├── src/
│   │   ├── App.tsx         # Main component
│   │   ├── api.ts          # REST client
│   │   ├── useWebSocket.ts # WebSocket hook
│   │   └── components/
│   │       ├── CrackerPanel.tsx  # WebGPU key cracking
│   │       ├── MapView.tsx       # Leaflet map showing node locations
│   │       └── ...
│   └── vite.config.ts
├── scripts/
│   ├── all_quality.sh      # Run all lint, format, typecheck, tests, build (parallelized)
│   ├── publish.sh          # Version bump, changelog, docker build & push
│   └── deploy.sh           # Deploy to production server
├── tests/                  # Backend tests (pytest)
├── data/                   # SQLite database (runtime)
└── pyproject.toml          # Python dependencies
```

## Development Setup

### Backend

```bash
# Install dependencies
uv sync

# Run server (auto-detects radio)
uv run uvicorn app.main:app --reload

# Or specify port
MESHCORE_SERIAL_PORT=/dev/cu.usbserial-0001 uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173, proxies /api to :8000
```

### Both Together (Development)

Terminal 1: `uv run uvicorn app.main:app --reload`
Terminal 2: `cd frontend && npm run dev`

### Production

In production, the FastAPI backend serves the compiled frontend. Build the frontend first:

```bash
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`. All API routes are prefixed with `/api`.

If `frontend/dist` (or `frontend/dist/index.html`) is missing, backend startup now logs an explicit error and continues serving API routes. In that case, frontend static routes are not mounted until a frontend build is present.

## Testing

### Backend (pytest)

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Key test files:
- `tests/test_decoder.py` - Channel + direct message decryption, key exchange
- `tests/test_keystore.py` - Ephemeral key store
- `tests/test_event_handlers.py` - ACK tracking, repeat detection
- `tests/test_packet_pipeline.py` - End-to-end packet processing
- `tests/test_api.py` - API endpoints, read state tracking
- `tests/test_migrations.py` - Database migration system
- `tests/test_frontend_static.py` - Frontend static route registration (missing `dist`/`index.html` handling)
- `tests/test_rx_log_data.py` - on_rx_log_data event handler integration
- `tests/test_ack_tracking_wiring.py` - DM ACK tracking extraction and wiring
- `tests/test_health_mqtt_status.py` - Health endpoint MQTT status field
- `tests/test_community_mqtt.py` - Community MQTT publisher (JWT, packet format, hash, broadcast)

### Frontend (Vitest)

```bash
cd frontend
npm run test:run
```

### Before Completing Changes

**Always run `./scripts/all_quality.sh` before finishing any changes.** This runs all linting, formatting, type checking, tests, and builds in parallel, catching type mismatches, breaking changes, and compilation errors.

## API Summary

All endpoints are prefixed with `/api` (e.g., `/api/health`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Connection status |
| GET | `/api/radio/config` | Radio configuration |
| PATCH | `/api/radio/config` | Update name, location, radio params |
| PUT | `/api/radio/private-key` | Import private key to radio |
| POST | `/api/radio/advertise` | Send advertisement |
| POST | `/api/radio/reboot` | Reboot radio or reconnect if disconnected |
| POST | `/api/radio/reconnect` | Manual radio reconnection |
| GET | `/api/contacts` | List contacts |
| GET | `/api/contacts/repeaters/advert-paths` | List recent unique advert paths for all contacts |
| GET | `/api/contacts/{public_key}` | Get contact by public key or prefix |
| GET | `/api/contacts/{public_key}/detail` | Comprehensive contact profile (stats, name history, paths) |
| GET | `/api/contacts/{public_key}/advert-paths` | List recent unique advert paths for a contact |
| POST | `/api/contacts` | Create contact (optionally trigger historical DM decrypt) |
| DELETE | `/api/contacts/{public_key}` | Delete contact |
| POST | `/api/contacts/sync` | Pull from radio |
| POST | `/api/contacts/{public_key}/add-to-radio` | Push contact to radio |
| POST | `/api/contacts/{public_key}/remove-from-radio` | Remove contact from radio |
| POST | `/api/contacts/{public_key}/mark-read` | Mark contact conversation as read |
| POST | `/api/contacts/{public_key}/command` | Send CLI command to repeater |
| POST | `/api/contacts/{public_key}/reset-path` | Reset contact path to flood |
| POST | `/api/contacts/{public_key}/trace` | Trace route to contact |
| POST | `/api/contacts/{public_key}/repeater/login` | Log in to a repeater |
| POST | `/api/contacts/{public_key}/repeater/status` | Fetch repeater status telemetry |
| POST | `/api/contacts/{public_key}/repeater/lpp-telemetry` | Fetch CayenneLPP sensor data |
| POST | `/api/contacts/{public_key}/repeater/neighbors` | Fetch repeater neighbors |
| POST | `/api/contacts/{public_key}/repeater/acl` | Fetch repeater ACL |
| POST | `/api/contacts/{public_key}/repeater/radio-settings` | Fetch radio settings via CLI |
| POST | `/api/contacts/{public_key}/repeater/advert-intervals` | Fetch advert intervals |
| POST | `/api/contacts/{public_key}/repeater/owner-info` | Fetch owner info |

| GET | `/api/channels` | List channels |
| GET | `/api/channels/{key}` | Get channel by key |
| POST | `/api/channels` | Create channel |
| DELETE | `/api/channels/{key}` | Delete channel |
| POST | `/api/channels/sync` | Pull from radio |
| POST | `/api/channels/{key}/mark-read` | Mark channel as read |
| GET | `/api/messages` | List with filters |
| POST | `/api/messages/direct` | Send direct message |
| POST | `/api/messages/channel` | Send channel message |
| POST | `/api/messages/channel/{message_id}/resend` | Resend an outgoing channel message (within 30 seconds) |
| GET | `/api/packets/undecrypted/count` | Count of undecrypted packets |
| POST | `/api/packets/decrypt/historical` | Decrypt stored packets |
| POST | `/api/packets/maintenance` | Delete old packets and vacuum |
| GET | `/api/read-state/unreads` | Server-computed unread counts, mentions, last message times |
| POST | `/api/read-state/mark-all-read` | Mark all conversations as read |
| GET | `/api/settings` | Get app settings |
| PATCH | `/api/settings` | Update app settings |
| POST | `/api/settings/favorites/toggle` | Toggle favorite status |
| POST | `/api/settings/migrate` | One-time migration from frontend localStorage |
| GET | `/api/statistics` | Aggregated mesh network statistics |
| WS | `/api/ws` | Real-time updates |

## Key Concepts

### Contact Public Keys

- Full key: 64-character hex string
- Prefix: 12-character hex (used for matching)
- Lookups use `LIKE 'prefix%'` for matching

### Contact Types

- `0` - Unknown
- `1` - Client (regular node)
- `2` - Repeater
- `3` - Room

### Channel Keys

- Stored as 32-character hex string (TEXT PRIMARY KEY)
- Hashtag channels: `SHA256("#name")[:16]` converted to hex
- Custom channels: User-provided or generated

### Message Types

- `PRIV` - Direct messages
- `CHAN` - Channel messages
- Both use `conversation_key` (user pubkey for PRIV, channel key for CHAN)

### Read State Tracking

Read state (`last_read_at`) is tracked **server-side** for consistency across devices:
- Stored as Unix timestamp in `contacts.last_read_at` and `channels.last_read_at`
- Updated via `POST /api/contacts/{public_key}/mark-read` and `POST /api/channels/{key}/mark-read`
- Bulk update via `POST /api/read-state/mark-all-read`
- Aggregated counts via `GET /api/read-state/unreads` (server-side computation)

**State Tracking Keys (Frontend)**: Generated by `getStateKey()` for message times (sidebar sorting):
- Channels: `channel-{channel_key}`
- Contacts: `contact-{full-public-key}`

**Note:** These are NOT the same as `Message.conversation_key` (the database field).

### MQTT Publishing

Optional MQTT integration forwards mesh events to an external broker for home automation, logging, or alerting. All MQTT config is stored in the database (`app_settings`), not env vars — configured from the Settings pane, no server restart needed.

**Two independent toggles**: publish decrypted messages, publish raw packets.

**Topic structure** (default prefix `meshcore`):
- `meshcore/dm:<contact_public_key>` — decrypted DM
- `meshcore/gm:<channel_key>` — decrypted channel message
- `meshcore/raw/dm:<contact_key>` — raw packet attributed to a DM contact
- `meshcore/raw/gm:<channel_key>` — raw packet attributed to a channel
- `meshcore/raw/unrouted` — raw packets that couldn't be attributed

**Architecture**: `broadcast_event()` in `websocket.py` calls `mqtt_broadcast()` — a single hook covering all message and raw_packet broadcasts. The `MqttPublisher` in `app/mqtt.py` manages a background connection loop with auto-reconnect and backoff. Publishes are fire-and-forget (silent drop if disconnected). Connection state changes trigger toasts via `broadcast_error`/`broadcast_success`. The health endpoint includes `mqtt_status`.

**Security**: MQTT password stored in plaintext in SQLite, consistent with the project's trusted-network design.

### Community MQTT Sharing

Separate from private MQTT, the community publisher (`app/community_mqtt.py`) shares raw packets with the MeshCore community aggregator for coverage mapping and analysis. Only raw packets are shared — never decrypted messages.

- Connects to community broker (default `mqtt-us-v1.letsmesh.net:443`) via WebSockets over TLS.
- Authentication via Ed25519 JWT signed with the radio's private key. Tokens auto-renew before 24h expiry.
- Broker address: separate `community_mqtt_broker_host` and `community_mqtt_broker_port` fields; defaults to `mqtt-us-v1.letsmesh.net:443`.
- Topic: `meshcore/{IATA}/{pubkey}/packets` — IATA is a 3-letter region code.
- JWT `email` claim enables node claiming on the community aggregator.
- Config: `community_mqtt_enabled`, `community_mqtt_iata`, `community_mqtt_broker_host`, `community_mqtt_broker_port`, `community_mqtt_email` in `app_settings`.

### Server-Side Decryption

The server can decrypt packets using stored keys, both in real-time and for historical packets.

**Channel messages**: Decrypted automatically when a matching channel key is available.

**Direct messages**: Decrypted server-side using the private key exported from the radio on startup. This enables DM decryption even when the contact isn't loaded on the radio. The private key is stored in memory only (see `keystore.py`).

## MeshCore Library

The `meshcore_py` library provides radio communication. Key patterns:

```python
# Connection
mc = await MeshCore.create_serial(port="/dev/ttyUSB0")

# Commands
await mc.commands.send_msg(dst, msg)
await mc.commands.send_chan_msg(channel_idx, msg)
await mc.commands.get_contacts()
await mc.commands.set_channel(idx, name, key)

# Events
mc.subscribe(EventType.CONTACT_MSG_RECV, handler)
mc.subscribe(EventType.CHANNEL_MSG_RECV, handler)
mc.subscribe(EventType.ACK, handler)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_SERIAL_PORT` | auto-detect | Serial port for radio |
| `MESHCORE_TCP_HOST` | *(none)* | TCP host for radio (mutually exclusive with serial/BLE) |
| `MESHCORE_TCP_PORT` | `4000` | TCP port (used with `MESHCORE_TCP_HOST`) |
| `MESHCORE_BLE_ADDRESS` | *(none)* | BLE device address (mutually exclusive with serial/TCP) |
| `MESHCORE_BLE_PIN` | *(required with BLE)* | BLE PIN code |
| `MESHCORE_SERIAL_BAUDRATE` | `115200` | Serial baud rate |
| `MESHCORE_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | SQLite database location |

**Note:** Runtime app settings are stored in the database (`app_settings` table), not environment variables. These include `max_radio_contacts`, `auto_decrypt_dm_on_advert`, `sidebar_sort_order`, `advert_interval`, `last_advert_time`, `favorites`, `last_message_times`, `bots`, all MQTT configuration (`mqtt_broker_host`, `mqtt_broker_port`, `mqtt_username`, `mqtt_password`, `mqtt_use_tls`, `mqtt_tls_insecure`, `mqtt_topic_prefix`, `mqtt_publish_messages`, `mqtt_publish_raw_packets`), and community MQTT configuration (`community_mqtt_enabled`, `community_mqtt_iata`, `community_mqtt_broker_host`, `community_mqtt_broker_port`, `community_mqtt_email`). They are configured via `GET/PATCH /api/settings` (and related settings endpoints).

Byte-perfect channel retries are user-triggered via `POST /api/messages/channel/{message_id}/resend` and are allowed for 30 seconds after the original send.

**Transport mutual exclusivity:** Only one of `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, or `MESHCORE_BLE_ADDRESS` may be set. If none are set, serial auto-detection is used.
