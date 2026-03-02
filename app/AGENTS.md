# Backend AGENTS.md

This document is the backend working guide for agents and developers.
Keep it aligned with `app/` source files and router behavior.

## Stack

- FastAPI
- aiosqlite
- Pydantic
- MeshCore Python library (`meshcore` from PyPI)
- PyCryptodome

## Backend Map

```text
app/
├── main.py              # App startup/lifespan, router registration, static frontend mounting
├── config.py            # Env-driven runtime settings
├── database.py          # SQLite connection + base schema + migration runner
├── migrations.py        # Schema migrations (SQLite user_version)
├── models.py            # Pydantic request/response models
├── repository/          # Data access layer (contacts, channels, messages, raw_packets, settings)
├── radio.py             # RadioManager + auto-reconnect monitor
├── radio_sync.py        # Polling, sync, periodic advertisement loop
├── decoder.py           # Packet parsing/decryption
├── packet_processor.py  # Raw packet pipeline, dedup, path handling
├── event_handlers.py    # MeshCore event subscriptions and ACK tracking
├── websocket.py         # WS manager + broadcast helpers
├── mqtt_base.py         # Shared MQTT publisher base class (lifecycle, reconnect, backoff)
├── mqtt.py              # Private MQTT publisher (fire-and-forget forwarding)
├── community_mqtt.py    # Community MQTT publisher (raw packet sharing)
├── bot.py               # Bot execution and outbound bot sends
├── dependencies.py      # Shared FastAPI dependency providers
├── keystore.py          # Ephemeral private/public key storage for DM decryption
├── frontend_static.py   # Mount/serve built frontend (production)
└── routers/
    ├── health.py
    ├── radio.py
    ├── contacts.py
    ├── channels.py
    ├── messages.py
    ├── packets.py
    ├── read_state.py
    ├── settings.py
    ├── repeaters.py
    ├── statistics.py
    └── ws.py
```

## Core Runtime Flows

### Incoming data

1. Radio emits events.
2. `on_rx_log_data` stores raw packet and tries decrypt/pipeline handling.
3. Decrypted messages are inserted into `messages` and broadcast over WS.
4. `CONTACT_MSG_RECV` is a fallback DM path when packet pipeline cannot decrypt.

### Outgoing messages

1. Send endpoints in `routers/messages.py` call MeshCore commands.
2. Message is persisted as outgoing.
3. Endpoint broadcasts WS `message` event so all live clients update.
4. ACK/repeat updates arrive later as `message_acked` events.
5. Channel resend (`POST /messages/channel/{id}/resend`) strips the sender name prefix by exact match against the current radio name. This assumes the radio name hasn't changed between the original send and the resend — a safe assumption since name changes require a radio config update and are not something that happens mid-conversation.

### Connection lifecycle

- `RadioManager.start_connection_monitor()` checks health every 5s.
- Monitor reconnect path runs `post_connect_setup()` before broadcasting healthy state.
- Manual reconnect/reboot endpoints call `reconnect()` then `post_connect_setup()`.
- Setup includes handler registration, key export, time sync, contact/channel sync, polling/advert tasks.

## Important Behaviors

### Read/unread state

- Server is source of truth (`contacts.last_read_at`, `channels.last_read_at`).
- `GET /api/read-state/unreads` returns counts, mention flags, and `last_message_times`.

### Echo/repeat dedup

- Message uniqueness: `(type, conversation_key, text, sender_timestamp)`.
- Duplicate insert is treated as an echo/repeat: the new path (if any) is appended, and the ACK count is incremented **only for outgoing messages**. Incoming repeats add path data but do not change the ACK count.

### Raw packet dedup policy

- Raw packet storage deduplicates by payload hash (`RawPacketRepository.create`), excluding routing/path bytes.
- Stored packet `id` is therefore a payload identity, not a per-arrival identity.
- Realtime raw-packet WS broadcasts include `observation_id` (unique per RF arrival) in addition to `id`.
- Frontend packet-feed features should key/dedupe by `observation_id`; use `id` only as the storage reference.
- Message-layer repeat handling (`_handle_duplicate_message` + `MessageRepository.add_path`) is separate from raw-packet storage dedup.

### Contact sync throttle

- `sync_recent_contacts_to_radio()` sets `_last_contact_sync = now` before the sync completes.
- This is intentional: if sync fails, the next attempt is still throttled to prevent a retry-storm against a flaky radio. Contacts will resync on the next scheduled cycle or on reconnect.

### Periodic advertisement

- Controlled by `app_settings.advert_interval` (seconds).
- `0` means disabled.
- Last send time tracked in `app_settings.last_advert_time`.

### MQTT publishing

- Optional forwarding of mesh events to an external MQTT broker.
- All config in `app_settings` (not env vars): `mqtt_broker_host`, `mqtt_broker_port`, `mqtt_username`, `mqtt_password`, `mqtt_use_tls`, `mqtt_topic_prefix`, `mqtt_publish_messages`, `mqtt_publish_raw_packets`.
- Disabled when `mqtt_broker_host` is empty.
- `broadcast_event()` in `websocket.py` calls `mqtt_broadcast()` — single hook covers all message and raw_packet events.
- `MqttPublisher` (`app/mqtt.py`) runs a background connection loop with auto-reconnect and exponential backoff (5s → 30s).
- Publishes are fire-and-forget; individual publish failures logged but not surfaced to users.
- Connection state changes surface via `broadcast_error`/`broadcast_success` toasts.
- Health endpoint includes `mqtt_status` field (`connected`, `disconnected`, `disabled`).
- Settings changes trigger `mqtt_publisher.restart()` — no server restart needed.
- Topics: `{prefix}/dm:{key}`, `{prefix}/gm:{key}`, `{prefix}/raw/dm:{key}`, `{prefix}/raw/gm:{key}`, `{prefix}/raw/unrouted`.

### Community MQTT

- Separate publisher (`app/community_mqtt.py`) for sharing raw packets with the MeshCore community aggregator.
- Independent from the private `MqttPublisher` — different broker, authentication, and topic structure.
- Connects to the community broker (default `mqtt-us-v1.letsmesh.net:443`) via WebSockets over TLS.
- Authentication: Ed25519 JWT tokens signed with the radio's expanded "orlp" private key. Tokens expire after 24 hours; proactive renewal at 23 hours.
- Broker address: separate `community_mqtt_broker_host` and `community_mqtt_broker_port` fields; defaults to `mqtt-us-v1.letsmesh.net:443`.
- JWT claims include `publicKey`, `owner` (radio pubkey), `client` (app identifier), and optional `email` (for node claiming on the community aggregator).
- Topic: `meshcore/{IATA}/{pubkey}/packets` — IATA is a 3-letter region code (required to enable; no default).
- Only raw packets are published — never decrypted messages.
- Publishes are fire-and-forget. The connection loop detects publish failures via `connected` flag and reconnects within 60 seconds.
- Health endpoint includes `community_mqtt_status` field.
- Settings: `community_mqtt_enabled`, `community_mqtt_iata`, `community_mqtt_broker_host`, `community_mqtt_broker_port`, `community_mqtt_email`.

## API Surface (all under `/api`)

### Health
- `GET /health`

### Radio
- `GET /radio/config`
- `PATCH /radio/config`
- `PUT /radio/private-key`
- `POST /radio/advertise`
- `POST /radio/reboot`
- `POST /radio/reconnect`

### Contacts
- `GET /contacts`
- `GET /contacts/repeaters/advert-paths` — recent advert paths for all contacts
- `GET /contacts/{public_key}`
- `GET /contacts/{public_key}/detail` — comprehensive contact profile (stats, name history, paths, nearest repeaters)
- `GET /contacts/{public_key}/advert-paths` — recent advert paths for one contact
- `POST /contacts`
- `DELETE /contacts/{public_key}`
- `POST /contacts/sync`
- `POST /contacts/{public_key}/add-to-radio`
- `POST /contacts/{public_key}/remove-from-radio`
- `POST /contacts/{public_key}/mark-read`
- `POST /contacts/{public_key}/command`
- `POST /contacts/{public_key}/reset-path`
- `POST /contacts/{public_key}/trace`
- `POST /contacts/{public_key}/repeater/login`
- `POST /contacts/{public_key}/repeater/status`
- `POST /contacts/{public_key}/repeater/lpp-telemetry`
- `POST /contacts/{public_key}/repeater/neighbors`
- `POST /contacts/{public_key}/repeater/acl`
- `POST /contacts/{public_key}/repeater/radio-settings`
- `POST /contacts/{public_key}/repeater/advert-intervals`
- `POST /contacts/{public_key}/repeater/owner-info`

### Channels
- `GET /channels`
- `GET /channels/{key}`
- `POST /channels`
- `DELETE /channels/{key}`
- `POST /channels/sync`
- `POST /channels/{key}/mark-read`

### Messages
- `GET /messages`
- `POST /messages/direct`
- `POST /messages/channel`
- `POST /messages/channel/{message_id}/resend`

### Packets
- `GET /packets/undecrypted/count`
- `POST /packets/decrypt/historical`
- `POST /packets/maintenance`

### Read state
- `GET /read-state/unreads`
- `POST /read-state/mark-all-read`

### Settings
- `GET /settings`
- `PATCH /settings`
- `POST /settings/favorites/toggle`
- `POST /settings/migrate`

### Statistics
- `GET /statistics` — aggregated mesh network stats (entity counts, message/packet splits, activity windows, busiest channels)

### WebSocket
- `WS /ws`

## WebSocket Events

- `health` — radio connection status (broadcast on change, personal on connect)
- `contact` — single contact upsert (from advertisements and radio sync)
- `message` — new message (channel or DM, from packet processor or send endpoints)
- `message_acked` — ACK/echo update for existing message (ack count + paths)
- `raw_packet` — every incoming RF packet (for real-time packet feed UI)
- `error` — toast notification (reconnect failure, missing private key, etc.)
- `success` — toast notification (historical decrypt complete, etc.)

Initial WS connect sends `health` only. Contacts/channels are loaded by REST.
Client sends `"ping"` text; server replies `{"type":"pong"}`.

## Data Model Notes

Main tables:
- `contacts` (includes `first_seen` for contact age tracking)
- `channels`
- `messages` (includes `sender_name`, `sender_key` for per-contact channel message attribution)
- `raw_packets`
- `contact_advert_paths` (recent unique advertisement paths per contact)
- `contact_name_history` (tracks name changes over time)
- `app_settings`

`app_settings` fields in active model:
- `max_radio_contacts`
- `favorites`
- `auto_decrypt_dm_on_advert`
- `sidebar_sort_order`
- `last_message_times`
- `preferences_migrated`
- `advert_interval`
- `last_advert_time`
- `bots`
- `mqtt_broker_host`, `mqtt_broker_port`, `mqtt_username`, `mqtt_password`
- `mqtt_use_tls`, `mqtt_tls_insecure`, `mqtt_topic_prefix`, `mqtt_publish_messages`, `mqtt_publish_raw_packets`
- `community_mqtt_enabled`, `community_mqtt_iata`, `community_mqtt_broker_host`, `community_mqtt_broker_port`, `community_mqtt_email`

## Security Posture (intentional)

- No authn/authz.
- No CORS restriction (`*`).
- Bot code executes user-provided Python via `exec()`.

These are product decisions for trusted-network deployments; do not flag as accidental vulnerabilities.

## Testing

Run backend tests:

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Test suites:

```text
tests/
├── conftest.py                 # Shared fixtures
├── test_ack_tracking_wiring.py # DM ACK tracking extraction and wiring
├── test_api.py                 # REST endpoint integration tests
├── test_bot.py                 # Bot execution and sandboxing
├── test_channels_router.py     # Channels router endpoints
├── test_config.py              # Configuration validation
├── test_contacts_router.py     # Contacts router endpoints
├── test_decoder.py             # Packet parsing/decryption
├── test_echo_dedup.py          # Echo/repeat deduplication (incl. concurrent)
├── test_event_handlers.py      # ACK tracking, event registration, cleanup
├── test_frontend_static.py     # Frontend static file serving
├── test_health_mqtt_status.py  # Health endpoint MQTT status field
├── test_key_normalization.py   # Public key normalization
├── test_keystore.py            # Ephemeral keystore
├── test_message_pagination.py  # Cursor-based message pagination
├── test_message_prefix_claim.py # Message prefix claim logic
├── test_migrations.py          # Schema migration system
├── test_community_mqtt.py      # Community MQTT publisher (JWT, packet format, hash, broadcast)
├── test_mqtt.py                # MQTT publisher topic routing and lifecycle
├── test_packet_pipeline.py     # End-to-end packet processing
├── test_packets_router.py      # Packets router endpoints (decrypt, maintenance)
├── test_radio.py               # RadioManager, serial detection
├── test_radio_operation.py     # radio_operation() context manager
├── test_radio_router.py        # Radio router endpoints
├── test_radio_sync.py          # Polling, sync, advertisement
├── test_repeater_routes.py     # Repeater command/telemetry/trace + granular pane endpoints
├── test_repository.py          # Data access layer
├── test_rx_log_data.py         # on_rx_log_data event handler integration
├── test_send_messages.py       # Outgoing messages, bot triggers, concurrent sends
├── test_settings_router.py     # Settings endpoints, advert validation
├── test_statistics.py          # Statistics aggregation
├── test_websocket.py           # WS manager broadcast/cleanup
└── test_websocket_route.py     # WS endpoint lifecycle
```

## Errata & Known Non-Issues

### Sender timestamps are 1-second resolution (protocol constraint)

The MeshCore radio protocol encodes `sender_timestamp` as a 4-byte little-endian integer (Unix seconds). This is a firmware-level wire format — the radio, the Python library (`commands/messaging.py`), and the decoder (`decoder.py`) all read/write exactly 4 bytes. Millisecond Unix timestamps would overflow 4 bytes, so higher resolution is not possible without a firmware change.

**Consequence:** The dedup index `(type, conversation_key, text, COALESCE(sender_timestamp, 0))` operates at 1-second granularity. Sending identical text to the same conversation twice within one second will hit the UNIQUE constraint on the second insert, returning HTTP 500 *after* the radio has already transmitted. The message is sent over the air but not stored in the database. Do not attempt to fix this by switching to millisecond timestamps — it will break echo dedup (the echo's 4-byte timestamp won't match the stored value) and overflow `to_bytes(4, "little")`.

### Outgoing DM echoes remain undecrypted

When our own outgoing DM is heard back via `RX_LOG_DATA` (self-echo, loopback), `_process_direct_message` passes `our_public_key=None` for the outgoing direction, disabling the outbound hash check in the decoder. The decoder's inbound check (`src_hash == their_first_byte`) fails because the source is us, not the contact — so decryption returns `None`. This is by design: outgoing DMs are stored directly by the send endpoint, so no message is lost.

### Infinite setup retry on connection monitor

When `post_connect_setup()` fails (e.g. `export_and_store_private_key` raises `RuntimeError` because the radio didn't respond), `_setup_complete` is never set to `True`. The connection monitor sees `connected and not setup_complete` and retries every 5 seconds — indefinitely. This is intentional: the radio may be rebooting, waking from sleep, or otherwise temporarily unresponsive. We keep retrying so that setup completes automatically once the radio becomes available, without requiring manual intervention.

### DELETE channel returns 200 for non-existent keys

`DELETE /api/channels/{key}` returns `{"status": "ok"}` even if the key didn't exist. This is intentional — the postcondition is "channel doesn't exist," which is satisfied regardless of whether it existed before. No 404 needed.

### Contact lat/lon 0.0 vs NULL

MeshCore uses `0.0` as the sentinel for "no GPS coordinates" (see `models.py` `to_radio_dict`). The upsert SQL uses `COALESCE(excluded.lat, contacts.lat)`, which preserves existing values when the new value is `NULL` — but `0.0` is not `NULL`, so it overwrites previously valid coordinates. This is intentional: we always want the most recent location data. If a device stops broadcasting GPS, the old coordinates are presumably stale/wrong, so overwriting with "not available" (`0.0`) is the correct behavior.

## Editing Checklist

When changing backend behavior:
1. Update/add router and repository tests.
2. Confirm WS event contracts when payload shape changes.
3. Run `PYTHONPATH=. uv run pytest tests/ -v`.
4. If API contract changed, update frontend types and AGENTS docs.
