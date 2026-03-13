# Health API

Server and radio connection status. Used by the UI and for automation (e.g. checking if SPI setup is required).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Server and radio status |

## GET /api/health

Returns connection status, database size, fanout statuses, and (in SPI mode) whether provisioning is required.

**Response (200):**

```json
{
  "status": "ok",
  "radio_connected": true,
  "radio_initializing": false,
  "connection_info": "SPI: waveshare",
  "database_size_mb": 0.12,
  "oldest_undecrypted_timestamp": 1773259096,
  "fanout_statuses": {},
  "bots_disabled": false,
  "setup_required": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` when radio is connected and setup complete; `"degraded"` otherwise |
| `radio_connected` | boolean | Whether the radio transport is connected |
| `radio_initializing` | boolean | True while post-connect setup (sync, key export, etc.) is in progress |
| `connection_info` | string \| null | Human-readable transport (e.g. `"Serial: /dev/ttyUSB0"`, `"SPI: waveshare"`, `"TCP: 192.168.1.1:4000"`) |
| `database_size_mb` | number | SQLite database size in MB |
| `oldest_undecrypted_timestamp` | number \| null | Unix time of oldest undecrypted packet, or null |
| `fanout_statuses` | object | Status of each fanout module (MQTT, etc.) by UUID |
| `bots_disabled` | boolean | Whether the bot system is disabled (`MESHCORE_DISABLE_BOTS`) |
| `setup_required` | boolean | **SPI only.** True when SPI config is missing or invalid; frontend can show setup wizard |
