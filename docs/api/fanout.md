# Fanout API

Fanout modules (MQTT, bots, webhooks, Apprise) are configured here. Each module has a type, name, and scope-based event filtering. See `app/fanout/AGENTS_fanout.md` for architecture.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/fanout` | List all fanout configs |
| POST | `/api/fanout` | Create new fanout config |
| PATCH | `/api/fanout/{config_id}` | Update config (triggers module reload) |
| DELETE | `/api/fanout/{config_id}` | Delete config (stops module) |

## GET /api/fanout

**Response (200):** Array of fanout config objects (id, name, type, status, scopes, etc.). Types include `mqtt_private`, `mqtt_community`, `webhook`, `apprise`, `bot`.

## POST /api/fanout

**Request body (JSON):** Config fields depend on type (e.g. MQTT broker URL, topic; webhook URL; bot code). Required: `name`, `type`, and type-specific options.

**Response (200):** Created config. Module starts if valid.

## PATCH /api/fanout/{config_id}

**Request body (JSON):** Partial config. Updating triggers reload of that module.

**Response (200):** Updated config.

## DELETE /api/fanout/{config_id}

Removes the config and stops the module.

**Response (200):** Acknowledgment.
