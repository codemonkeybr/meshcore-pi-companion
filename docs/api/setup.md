# Setup API (SPI provisioning)

Endpoints for first-boot or re-provisioning when using the **SPI backend** (Raspberry Pi + LoRa HAT). Used by the CLI wizard and (when implemented) the web setup wizard.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/setup/status` | Whether provisioning is required and config path |
| GET | `/api/setup/hardware-profiles` | List supported LoRa HAT profiles |
| GET | `/api/setup/radio-presets` | Region/radio presets (from API or fallback JSON) |
| POST | `/api/setup/provision` | Write or update SPI config and persist |

## GET /api/setup/status

Indicates if the node needs provisioning (no or invalid `config.yaml` in SPI mode).

**Response (200):**

When no SPI config path exists (e.g. serial/TCP mode):

```json
{
  "setup_required": false,
  "mode": "serial"
}
```

When SPI config path exists but is invalid or missing:

```json
{
  "setup_required": true,
  "mode": "spi",
  "config_path": "/home/pi/remoteterm/config.yaml"
}
```

When SPI config is valid:

```json
{
  "setup_required": false,
  "mode": "spi",
  "config_path": "/home/pi/remoteterm/config.yaml"
}
```

## GET /api/setup/hardware-profiles

Returns supported hardware profiles (Waveshare, uConsole, PiMesh-1W, meshadv, HT-RA62, etc.) with pin and prerequisite info.

**Response (200):** Array of objects, e.g.:

```json
[
  {
    "id": "waveshare",
    "name": "Waveshare LoRa HAT (SPI)",
    "bus_id": 0,
    "cs_pin": 21,
    "reset_pin": 18,
    "busy_pin": 20,
    "irq_pin": 16,
    "txen_pin": 13,
    "rxen_pin": 12,
    "prerequisites": ["Enable SPI via raspi-config"],
    "notes": "..."
  }
]
```

## GET /api/setup/radio-presets

Returns radio presets (frequency, spreading factor, bandwidth, coding rate) for region selection. Fetched from `https://api.meshcore.nz/api/v1/config` when online; falls back to `data/radio-presets-fallback.json` when offline.

**Response (200):** Array of preset objects, e.g.:

```json
[
  {
    "title": "EU 868",
    "frequency": 868.1,
    "spreading_factor": 7,
    "bandwidth": 125,
    "coding_rate": 5
  }
]
```

## POST /api/setup/provision

Writes or updates the SPI config file (`config.yaml` or `data/config.yaml`). Does not generate identity; identity is created on first SPI connect via `app/spi_identity`.

**Request body (JSON):**

| Key | Type | Description |
|-----|------|-------------|
| `node_name` | string | Node display name |
| `hardware_profile` | string | Profile id from `/api/setup/hardware-profiles` (e.g. `"waveshare"`) |
| `radio_preset` | object | Preset from `/api/setup/radio-presets` (frequency in MHz, etc.) |
| `frequency_hz` | int | Override frequency in Hz |
| `bandwidth_hz` | int | Override bandwidth in Hz |
| `spreading_factor` | int | Override spreading factor |
| `coding_rate` | int | Override coding rate |
| `latitude` | number | Node latitude |
| `longitude` | number | Node longitude |

**Response (200):**

```json
{
  "status": "ok",
  "config_path": "/home/pi/remoteterm/config.yaml"
}
```

**Errors:** 400 if `hardware_profile` is unknown or `radio_preset` is invalid.
