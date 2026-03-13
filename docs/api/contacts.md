# Contacts API

Contacts are mesh nodes (clients, repeaters, rooms, sensors) identified by public key. The server stores them in the database and can sync to/from the radio (client backend) or manage them locally (SPI backend).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/contacts` | List all contacts |
| GET | `/api/contacts/repeaters/advert-paths` | Advert paths summary for repeaters |
| GET | `/api/contacts/{public_key}` | Get contact by public key or prefix |
| GET | `/api/contacts/{public_key}/detail` | Full contact profile (stats, name history, paths) |
| GET | `/api/contacts/{public_key}/advert-paths` | Recent advert paths for this contact |
| POST | `/api/contacts` | Create contact (optional historical DM decrypt) |
| DELETE | `/api/contacts/{public_key}` | Delete contact |
| POST | `/api/contacts/sync` | Pull contacts from radio |
| POST | `/api/contacts/{public_key}/add-to-radio` | Push contact to radio |
| POST | `/api/contacts/{public_key}/remove-from-radio` | Remove contact from radio |
| POST | `/api/contacts/{public_key}/mark-read` | Mark conversation as read |
| POST | `/api/contacts/{public_key}/command` | Send CLI command (repeater) |
| POST | `/api/contacts/{public_key}/routing-override` | Set or clear routing override |
| POST | `/api/contacts/{public_key}/trace` | Trace route to contact |
| POST | `/api/contacts/{public_key}/repeater/login` | Log in to repeater |
| POST | `/api/contacts/{public_key}/repeater/status` | Repeater status telemetry |
| POST | `/api/contacts/{public_key}/repeater/lpp-telemetry` | CayenneLPP sensor data |
| POST | `/api/contacts/{public_key}/repeater/neighbors` | Repeater neighbors |
| POST | `/api/contacts/{public_key}/repeater/acl` | Repeater ACL |
| POST | `/api/contacts/{public_key}/repeater/radio-settings` | Radio settings via CLI |
| POST | `/api/contacts/{public_key}/repeater/advert-intervals` | Advert intervals |
| POST | `/api/contacts/{public_key}/repeater/owner-info` | Owner info |

## Path parameters

- **public_key:** Full 64-character hex or 12-character prefix. Lookups use prefix matching when needed.

## GET /api/contacts

**Query:** Optional filters (e.g. type, search).  
**Response (200):** Array of `Contact` objects (public_key, name, type, last_path, on_radio, etc.).

## POST /api/contacts

Create a contact. Body can include `public_key`, `name`, `type`, and optionally trigger historical DM decryption for this key.

**Request body (JSON):** Contact fields; `public_key` required.

**Response (200):** Created `Contact`.

## Repeater endpoints

For contacts of type repeater (type=2), the repeater endpoints allow login, status, LPP telemetry, neighbors, ACL, radio settings, advert intervals, and owner info. Each returns data from the repeater firmware or an error.

## POST /api/contacts/{public_key}/trace

Trace route to the contact. Returns path/tag data.

**Response (200):** `TraceResponse` with trace result.

## POST /api/contacts/{public_key}/routing-override

Set or clear a forced routing override (path and length) for this contact. When set, outbound operations use the override instead of the learned path.

**Request body (JSON):** `{ "path": "<hex>", "path_len": N }` or `{}` to clear.
