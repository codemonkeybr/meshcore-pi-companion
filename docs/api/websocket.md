# WebSocket API

Real-time events (messages, raw packets, contact/channel updates, health, etc.) are pushed over a single WebSocket connection.

## Endpoint

| Method | Path | Description |
|--------|------|-------------|
| WS | `/api/ws` | WebSocket connection for real-time events |

## Connection

Connect to `ws://<host>:8000/api/ws` (or `wss://` when using HTTPS). The server may send an initial **health** message on connect. Subsequent messages are event-driven.

## Message format

Messages are JSON objects. Typical shape:

```json
{
  "type": "message",
  "data": { ... }
}
```

`type` identifies the event; `data` is the payload.

## Event types (examples)

| type | Description |
|------|-------------|
| `health` | Health status (same shape as GET /api/health) |
| `message` | New or updated message (e.g. incoming DM/channel, path update) |
| `message_acked` | DM ACK or channel repeat count update |
| `raw_packet` | Raw RF packet (for visualizer/debug); includes `observation_id` |
| `contact` | Contact added/updated/deleted |
| `channel` | Channel added/updated/deleted |
| `contact_deleted` | Contact removed (payload: `public_key`) |

Frontend uses `observation_id` on raw_packet events as the unique key for display; `id` is the storage identity (payload dedup). See AGENTS.md “Intentional Packet Handling Decision.”
