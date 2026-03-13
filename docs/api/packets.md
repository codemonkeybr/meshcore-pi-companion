# Packets API

Raw packet storage, undecrypted count, historical decryption, and maintenance (delete old packets, vacuum).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/packets/undecrypted/count` | Count of undecrypted packets |
| POST | `/api/packets/decrypt/historical` | Decrypt stored packets (using current keys) |
| POST | `/api/packets/maintenance` | Delete old packets and vacuum database |

## GET /api/packets/undecrypted/count

**Response (200):** Object with count of raw packets that could not be decrypted (e.g. `{ "count": 42 }`). Useful for UI badges and triggering historical decrypt.

## POST /api/packets/decrypt/historical

Attempt to decrypt stored raw packets using current keys (contacts, channels, private key). Newly decrypted messages are inserted and broadcast.

**Request body (JSON):** Optional limits (e.g. max packets, max age).

**Response (200):** Decrypt result (e.g. decrypted count, errors).

## POST /api/packets/maintenance

Delete old raw packets and run SQLite VACUUM. Request body can specify retention (e.g. keep last N days).

**Response (200):** Maintenance result (e.g. deleted count).
