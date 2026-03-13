# Read state API

Server-side read/unread state for conversations. Used for unread badges and “mark all as read.”

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/read-state/unreads` | Unread counts, mentions, last message times per conversation |
| POST | `/api/read-state/mark-all-read` | Mark all conversations as read |

## GET /api/read-state/unreads

Returns server-computed unread counts and related data for all conversations (contacts and channels). Respects blocked keys/names.

**Response (200):** Object keyed by conversation (e.g. `contact-{public_key}`, `channel-{key}`) with unread count, mention flag, last message time, etc.

## POST /api/read-state/mark-all-read

Sets `last_read_at` to now for all contacts and channels.

**Response (200):** Acknowledgment.
