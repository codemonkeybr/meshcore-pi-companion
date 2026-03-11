# Messages API

Direct (DM) and channel messages. Messages are stored in the database; sending goes through the radio backend.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/messages` | List messages with filters and pagination |
| GET | `/api/messages/around/{message_id}` | Messages around a given message (jump-to-message) |
| POST | `/api/messages/direct` | Send direct message |
| POST | `/api/messages/channel` | Send channel message |
| POST | `/api/messages/channel/{message_id}/resend` | Resend channel message |

## GET /api/messages

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `q` | string | Search filter (optional) |
| `after` | int | Unix timestamp; return messages after this time |
| `after_id` | int | Message ID; return messages after this id (forward pagination) |
| `type` | string | `PRIV` or `CHAN` |
| `conversation_key` | string | Filter by conversation (contact public key or channel key) |
| `limit` | int | Max messages to return |

**Response (200):** Array of `Message` objects (id, type, conversation_key, text, sender_timestamp, paths, outgoing, etc.).

## GET /api/messages/around/{message_id}

Returns messages around a specific message (for “jump to message” UI). Includes the message with that id and surrounding messages.

**Response (200):** Object with `messages` array and context (e.g. `before`, `after`).

## POST /api/messages/direct

Send a direct message to a contact.

**Request body (JSON):**

| Key | Type | Description |
|-----|------|-------------|
| `destination` | string | Contact public key or prefix |
| `text` | string | Message body |

**Response (200):** Created `Message` (outgoing). ACK and path updates arrive later via WebSocket.

## POST /api/messages/channel

Send a channel message.

**Request body (JSON):**

| Key | Type | Description |
|-----|------|-------------|
| `channel_key` | string | 32-char hex channel key |
| `text` | string | Message body |

**Response (200):** Created `Message` (outgoing).

## POST /api/messages/channel/{message_id}/resend

Resend a channel message. Default: byte-perfect resend within 30 seconds of original. Query `?new_timestamp=true`: new timestamp, no time limit, creates a new message row.

**Response (200):** Resent message or new message depending on parameters.
