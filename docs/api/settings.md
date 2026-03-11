# Settings API

Application settings (sidebar sort, advert interval, favorites, blocked keys/names, etc.). Stored in the database (`app_settings` table), not environment variables.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Get all app settings |
| PATCH | `/api/settings` | Update settings (partial) |
| POST | `/api/settings/favorites/toggle` | Toggle favorite status for a contact/channel |
| POST | `/api/settings/blocked-keys/toggle` | Toggle blocked key |
| POST | `/api/settings/blocked-names/toggle` | Toggle blocked name |
| POST | `/api/settings/migrate` | One-time migration from frontend localStorage |

## GET /api/settings

**Response (200):** `AppSettings` object: `max_radio_contacts`, `auto_decrypt_dm_on_advert`, `sidebar_sort_order`, `advert_interval`, `last_advert_time`, `favorites`, `last_message_times`, `flood_scope`, `blocked_keys`, `blocked_names`, etc.

## PATCH /api/settings

**Request body (JSON):** Partial object; only provided fields are updated.

**Response (200):** Updated `AppSettings`.

## POST /api/settings/favorites/toggle

**Request body (JSON):** Identifies a conversation (e.g. by key or public_key). Toggles its presence in the favorites list.

**Response (200):** Updated `AppSettings`.

## POST /api/settings/blocked-keys/toggle

**Request body (JSON):** Public key to toggle in blocked list.

**Response (200):** Updated `AppSettings`.

## POST /api/settings/blocked-names/toggle

**Request body (JSON):** Name to toggle in blocked-names list.

**Response (200):** Updated `AppSettings`.

## POST /api/settings/migrate

One-time migration: import preferences from frontend localStorage format into server-side settings. Idempotent; safe to call multiple times.

**Request body (JSON):** Payload matching legacy frontend storage shape.

**Response (200):** Migration result (e.g. counts migrated).
