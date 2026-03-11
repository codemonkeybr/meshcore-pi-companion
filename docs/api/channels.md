# Channels API

Channels are group conversations (hashtag or custom). Keys are 32-character hex; hashtag channels derive key from `SHA256("#name")`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels` | List all channels |
| GET | `/api/channels/{key}` | Get channel by key |
| GET | `/api/channels/{key}/detail` | Channel profile (message stats, top senders) |
| POST | `/api/channels` | Create channel |
| DELETE | `/api/channels/{key}` | Delete channel |
| POST | `/api/channels/sync` | Pull channels from radio |
| POST | `/api/channels/{key}/mark-read` | Mark channel as read |
| POST | `/api/channels/{key}/flood-scope-override` | Set or clear per-channel flood-scope override |

## Path parameters

- **key:** 32-character hex channel key (primary key).

## GET /api/channels

**Response (200):** Array of `Channel` objects (key, name, is_hashtag, on_radio, etc.).

## GET /api/channels/{key}/detail

**Response (200):** `ChannelDetail` with message counts, top senders, and channel metadata.

## POST /api/channels

**Request body (JSON):** `key` (optional; can be generated), `name`, `is_hashtag`.

**Response (200):** Created `Channel`.

## POST /api/channels/{key}/flood-scope-override

Set or clear a per-channel flood-scope override. When set, channel sends use this scope for the duration of the send.

**Request body (JSON):** `{ "flood_scope": "region" }` or `{}` / `null` to clear.

**Response (200):** Updated `Channel`.
