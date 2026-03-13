# Radio API

Radio configuration, advertisement, reboot, reconnect, and private key import. Applies to both client (serial/TCP/BLE) and SPI backends where supported.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/radio/config` | Current radio configuration |
| PATCH | `/api/radio/config` | Update name, location, radio params, path_hash_mode |
| PUT | `/api/radio/private-key` | Import private key to radio |
| POST | `/api/radio/advertise` | Send advertisement |
| POST | `/api/radio/reboot` | Reboot radio or reconnect if disconnected |
| POST | `/api/radio/reconnect` | Manual reconnection |

## GET /api/radio/config

Returns current radio config (name, lat/lon, frequency, bandwidth, spreading factor, coding rate, tx power, path_hash_mode, etc.).

**Response (200):** Object with `name`, `latitude`, `longitude`, `frequency`, `bandwidth`, `spreading_factor`, `coding_rate`, `tx_power`, `path_hash_mode`, `path_hash_mode_supported`, and other fields.

## PATCH /api/radio/config

Update radio configuration. Body can include: `name`, `latitude`, `longitude`, `frequency`, `bandwidth`, `spreading_factor`, `coding_rate`, `tx_power`, `path_hash_mode` (when supported by firmware).

**Request body (JSON):** Partial object; only sent fields are updated.

**Response (200):** Updated radio config (same shape as GET).

## PUT /api/radio/private-key

Import a private key (hex string) to the radio. Replaces the node identity.

**Request body (JSON):** `{ "key": "<64-char hex>" }`

**Response:** 200 on success; 4xx on invalid key or radio error.

## POST /api/radio/advertise

Send an advertisement (flood or directed).

**Request body (JSON):** `{ "flood": true }` or `{ "flood": false }`

**Response (200):** `{ "status": "ok" }` or similar.

## POST /api/radio/reboot

Reboot the radio (firmware) or, for SPI, restart the dispatcher/radio stack. If disconnected, attempts reconnect.

**SPI backend:** The driver may not release GPIO until process exit. If auto-reconnect fails with "GPIO already in use", restart the app manually to clear the pin; the app does not exit on reboot.

**Response (200):** `{ "status": "ok", "message": "..." }`.

## POST /api/radio/reconnect

Trigger a manual reconnection to the radio without rebooting.

**Response (200):** Acknowledgment.
