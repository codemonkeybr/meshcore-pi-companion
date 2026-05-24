# Home Assistant Integration

RemoteTerm can publish mesh network data to Home Assistant via MQTT Discovery. Devices and entities appear automatically in HA -- no custom component or HACS install needed.

## Prerequisites

- Home Assistant with the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured
- An MQTT broker (e.g. Mosquitto) accessible to both HA and RemoteTerm
- RemoteTerm running and connected to a radio

## Setup

1. In RemoteTerm, go to **Settings > Integrations > Add > Home Assistant MQTT Discovery**
2. Enter your MQTT broker host and port (same broker HA is connected to)
3. Optionally enter broker username/password and TLS settings
4. Select contacts for GPS tracking and repeaters for telemetry (see below)
5. Configure which messages should fire events (scope selector at the bottom)
6. Save and enable

Devices will appear in HA under **Settings > Devices & Services > MQTT** within a few seconds.

## How MeshCore IDs Map Into Home Assistant

RemoteTerm uses each node's public key to derive a stable short identifier for MQTT topics:

- Full public key: `ae92577bae6c4f1d...`
- Node ID: `ae92577bae6c` (the first 12 hex characters, lowercased)
- Example MQTT topic: `meshcore/ae92577bae6c/gps`

When this README shows `<node_id>`, it always means that 12-character value. Node IDs appear in:

- MQTT discovery topics under `homeassistant/...`
- Runtime MQTT state topics under your configured prefix, usually `meshcore/...`

**Entity IDs** are different — HA auto-generates them from the device name and entity name, not from the node ID. For example, a radio named "MyRadio" produces entities like `binary_sensor.myradio_connected` and `event.myradio_messages`. A contact named "Alice" produces `device_tracker.alice`. You can find your actual entity IDs in **Settings > Devices & Services > MQTT** in HA, and you can rename them in HA's UI without affecting the integration.

You can also see the MQTT topic IDs in RemoteTerm's Home Assistant integration UI:

- `What gets created in Home Assistant`
- `Published topic summary`

## What Gets Created

### Local Radio Device

Always created. Updates every 60 seconds.

| Entity | Type | Description |
|--------|------|-------------|
| `binary_sensor.<radio_name>_connected` | Connectivity | Radio online/offline |
| `sensor.<radio_name>_noise_floor` | Signal strength | Radio noise floor (dBm) |

### Repeater Devices

One device per tracked repeater selected in the HA integration. Updates when telemetry is collected (auto-collect cycle (~8 hours or variable in settings), or when you manually fetch from the repeater dashboard).

Repeaters must first be added to the auto-telemetry tracking list in RemoteTerm's Radio settings section. Only auto-tracked repeaters appear in the HA integration's repeater picker.

| Entity | Type | Unit | Description |
|--------|------|------|-------------|
| `sensor.<repeater_name>_battery_voltage` | Voltage | V | Battery level |
| `sensor.<repeater_name>_noise_floor` | Signal strength | dBm | Local noise floor |
| `sensor.<repeater_name>_last_rssi` | Signal strength | dBm | Last received signal strength |
| `sensor.<repeater_name>_last_snr` | -- | dB | Last signal-to-noise ratio |
| `sensor.<repeater_name>_packets_received` | -- | count | Total packets received |
| `sensor.<repeater_name>_packets_sent` | -- | count | Total packets sent |
| `sensor.<repeater_name>_uptime` | Duration | s | Uptime since last reboot |

If RemoteTerm already has a cached telemetry snapshot for that repeater, it republishes it on startup so HA can populate the sensors immediately instead of waiting for the next collection cycle.

### Contact Device Trackers

One `device_tracker` per tracked contact. Updates passively whenever RemoteTerm hears an advertisement with GPS coordinates from that contact. No radio commands are sent -- it piggybacks on normal mesh traffic.

| Entity | Description |
|--------|-------------|
| `device_tracker.<contact_name>` | GPS position (latitude/longitude) |

### Message Event Entity

A single radio-scoped event entity, `event.<radio_name>_messages`, fires for each message matching your configured scope. Each event carries these attributes:

| Attribute | Example | Description |
|-----------|---------|-------------|
| `event_type` | `message_received` | Always `message_received` |
| `sender_name` | `Alice` | Display name of the sender |
| `sender_key` | `aabbccdd...` | Sender's public key |
| `text` | `hello` | Message body |
| `message_type` | `PRIV` or `CHAN` | Direct message or channel |
| `channel_name` | `#general` | Channel name (null for DMs) |
| `conversation_key` | `aabbccdd...` | Contact key (DM) or channel key |
| `outgoing` | `false` | Whether you sent this message |

## Entity Naming

HA auto-generates entity IDs by slugifying the device name and entity name. For a radio named "My Radio", entities look like `binary_sensor.my_radio_connected` and `event.my_radio_messages`. For a repeater named "Hilltop", `sensor.hilltop_battery_voltage`. For a contact named "Alice", `device_tracker.alice`. You can rename entities in HA's UI without affecting the integration.

MQTT topic paths use the 12-character node ID (first 12 hex characters of the public key). For example:

- Local radio health: `meshcore/<radio_node_id>/health`
- Repeater telemetry: `meshcore/<repeater_node_id>/telemetry`
- Contact GPS: `meshcore/<contact_node_id>/gps`
- Message events: `meshcore/<radio_node_id>/events/message`

## What Appears When

- Always created: the local radio device and its entities
- Created when selected in the HA integration: tracked repeater devices and tracked contact device trackers
- Populated only after data exists: contact GPS trackers need an advert with GPS; repeater sensors need telemetry, although cached repeater telemetry is replayed on startup when available
- Message event entity: always created once the HA integration is enabled for a connected radio

## Common Automations

### Low repeater battery alert

Notify when a tracked repeater's battery drops below a threshold.

**GUI:** Settings > Automations > Create > Numeric state trigger on `sensor.<repeater_name>_battery_voltage`, below `3.8`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Repeater battery low"
    trigger:
      - platform: numeric_state
        entity_id: sensor.hilltop_battery_voltage
        below: 3.8
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Repeater Battery Low"
          message: >-
            {{ state_attr('sensor.hilltop_battery_voltage', 'friendly_name') }}
            is at {{ states('sensor.hilltop_battery_voltage') }}V
```

### Radio offline alert

Notify if the radio has been disconnected for more than 5 minutes.

**GUI:** Settings > Automations > Create > State trigger on `binary_sensor.<radio_name>_connected`, to `off`, for `00:05:00`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Radio offline"
    trigger:
      - platform: state
        entity_id: binary_sensor.myradio_connected
        to: "off"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "MeshCore Radio Offline"
          message: "Radio has been disconnected for 5 minutes"
```

### Alert on any message from a specific room

Trigger when a message arrives in a specific channel. Two approaches:

#### Option A: Scope filtering (fully GUI, no template)

If you only care about one room, configure the HA integration's message scope to "Only listed channels" and select that room. Then every event fire is from that room.

**GUI:** Settings > Automations > Create > State trigger on `event.<radio_name>_messages`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Emergency channel alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Message in #emergency"
          message: >-
            {{ trigger.to_state.attributes.sender_name }}:
            {{ trigger.to_state.attributes.text }}
```

#### Option B: Template condition (multiple rooms, one integration)

Keep scope as "All messages" and filter in the automation. The trigger is GUI, but the condition uses a one-line template.

**GUI:** Settings > Automations > Create > State trigger on `event.<radio_name>_messages` > Add condition > Template > enter the template below.

**YAML:**
```yaml
automation:
  - alias: "Emergency channel alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.channel_name == '#emergency' }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Message in #emergency"
          message: >-
            {{ trigger.to_state.attributes.sender_name }}:
            {{ trigger.to_state.attributes.text }}
```

### Alert on DM from a specific contact

**YAML:**
```yaml
automation:
  - alias: "DM from Alice"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.message_type == 'PRIV'
             and trigger.to_state.attributes.sender_name == 'Alice' }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "DM from Alice"
          message: "{{ trigger.to_state.attributes.text }}"
```

### Alert on messages containing a keyword

**YAML:**
```yaml
automation:
  - alias: "Keyword alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ 'emergency' in trigger.to_state.attributes.text | lower }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Emergency keyword detected"
          message: >-
            {{ trigger.to_state.attributes.sender_name }} in
            {{ trigger.to_state.attributes.channel_name or 'DM' }}:
            {{ trigger.to_state.attributes.text }}
```

### Track a contact on the HA map

No automation needed. Once a contact is selected for GPS tracking, their `device_tracker` entity automatically appears on the HA map. Go to **Settings > Dashboards > Map** (or add a Map card to any dashboard) and the tracked contact will show up when they advertise their GPS position.

### Dashboard card showing repeater battery

Add a sensor card to any dashboard:

```yaml
type: sensor
entity: sensor.hilltop_battery_voltage
name: "Hilltop Repeater Battery"
```

Or an entities card for multiple repeaters:

```yaml
type: entities
title: "Repeater Status"
entities:
  - entity: sensor.hilltop_battery_voltage
    name: "Hilltop"
  - entity: sensor.valley_battery_voltage
    name: "Valley"
  - entity: sensor.ridge_battery_voltage
    name: "Ridge"
```

### Full monitoring dashboard with message feed

This example creates a dashboard with repeater vitals, a live message feed, and a network activity graph. Replace the three slug values below to match your setup — find your entity IDs in **Settings > Devices & Services > MQTT**.

```yaml
# ┌─────────────────────────────────────────────────────┐
# │  Replace these three values to match your entities  │
# │                                                     │
# │  radio_slug:    the prefix on your radio sensors    │
# │                 e.g. sensor.MYRADIO_noise_floor     │
# │  repeater_slug: the prefix on your repeater sensors │
# │                 e.g. sensor.HILLTOP_battery_voltage  │
# │  message_event: your message event entity ID        │
# │                 e.g. event.MYRADIO_messages          │
# └─────────────────────────────────────────────────────┘
#
#   radio_slug:    myradio
#   repeater_slug: hilltop
#   message_event: event.myradio_messages
```

**Step 1 — Dashboard YAML** (Settings > Dashboards > Add > edit in YAML):

```yaml
views:
  - title: MeshCore
    icon: mdi:radio-tower
    cards:
      - type: entities
        title: Hilltop — Current                     # ← repeater name
        state_color: true
        entities:
          - entity: sensor.hilltop_battery_voltage    # ← repeater_slug
            name: Battery
          - entity: sensor.hilltop_noise_floor        # ← repeater_slug
            name: Noise Floor
          - entity: sensor.hilltop_last_rssi          # ← repeater_slug
            name: Last RSSI
          - entity: sensor.hilltop_last_snr           # ← repeater_slug
            name: Last SNR
          - entity: sensor.hilltop_uptime             # ← repeater_slug
            name: Uptime
          - entity: sensor.hilltop_packets_received   # ← repeater_slug
            name: Packets Rx
          - entity: sensor.hilltop_packets_sent       # ← repeater_slug
            name: Packets Tx

      - type: statistics-graph
        title: Battery Voltage
        entities:
          - sensor.hilltop_battery_voltage            # ← repeater_slug
        stat_types: [mean, min, max]
        days_to_show: 7
        period: hour

      - type: statistics-graph
        title: Noise Floor
        entities:
          - sensor.hilltop_noise_floor                # ← repeater_slug
        stat_types: [mean, min, max]
        days_to_show: 7
        period: hour

      - type: markdown
        title: Message Feed (Last 10)
        content: |
          {% for i in range(1, 11) %}
          {% set msg = states('input_text.meshcore_msg_' ~ i) %}
          {% if msg and msg not in ['unknown', '', 'unavailable'] %}
          {{ msg }}

          {% endif %}
          {% endfor %}
          {% if states('input_text.meshcore_msg_1') in ['unknown', '', 'unavailable'] %}
          *No messages yet.*
          {% endif %}

      - type: statistics-graph
        title: Overall Packets Received
        entities:
          - sensor.myradio_packets_received           # ← radio_slug
        stat_types: [change]
        days_to_show: 7
        period: hour
```

**Step 2 — Message feed helpers**: create 10 text helpers named `MeshCore Msg 1` through `MeshCore Msg 10` (Settings > Helpers > Add > Text). These act as a rolling buffer for the Markdown card above.

**Step 3 — Message feed automation** (Settings > Automations > Create > edit in YAML):

```yaml
alias: MeshCore Message Feed Buffer
description: Rolling buffer of recent mesh messages for dashboard display
mode: queued
max: 10
triggers:
  - trigger: state
    entity_id: event.myradio_messages               # ← message_event
actions:
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_10
    data:
      value: "{{ states('input_text.meshcore_msg_9') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_9
    data:
      value: "{{ states('input_text.meshcore_msg_8') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_8
    data:
      value: "{{ states('input_text.meshcore_msg_7') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_7
    data:
      value: "{{ states('input_text.meshcore_msg_6') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_6
    data:
      value: "{{ states('input_text.meshcore_msg_5') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_5
    data:
      value: "{{ states('input_text.meshcore_msg_4') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_4
    data:
      value: "{{ states('input_text.meshcore_msg_3') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_3
    data:
      value: "{{ states('input_text.meshcore_msg_2') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_2
    data:
      value: "{{ states('input_text.meshcore_msg_1') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_1
    data:
      value: >-
        {{ as_timestamp(trigger.to_state.last_changed) |
        timestamp_custom('%-I:%M %p') }} |
        **{% if trigger.to_state.attributes.channel_name %}{{
        trigger.to_state.attributes.channel_name }}{% else %}DM{% endif %}** |
        {{ trigger.to_state.attributes.sender_name or 'Unknown' }}:
        {{ (trigger.to_state.attributes.text or '')[:180] }}
```

## Troubleshooting

### Devices don't appear in HA

- Verify the MQTT integration is configured in HA (**Settings > Devices & Services > MQTT**) and shows "Connected"
- Verify RemoteTerm's HA integration shows "Connected" (green dot)
- Check that both HA and RemoteTerm are using the same MQTT broker
- Subscribe to discovery topics to verify messages are flowing:
  ```
  mosquitto_sub -h <broker> -t 'homeassistant/#' -v
  ```

### Stale or duplicate devices

If you see unexpected devices (e.g. a generic "MeshCore Radio" alongside your named radio), clear the stale retained messages:
```
mosquitto_pub -h <broker> -t 'homeassistant/binary_sensor/meshcore_unknown/connected/config' -r -n
mosquitto_pub -h <broker> -t 'homeassistant/sensor/meshcore_unknown/noise_floor/config' -r -n
```

### Repeater sensors show "Unknown" or "Unavailable"

Repeater telemetry only updates when collected. Trigger a manual fetch by opening the repeater's dashboard in RemoteTerm and clicking "Status", or wait for the next auto-collect cycle (~8 hours).

If RemoteTerm already has cached telemetry for that repeater, it republishes the last known values on startup. If the sensors are still unknown or unavailable, it usually means no telemetry has ever been collected for that repeater yet.

### Contact device tracker shows "Unknown"

The contact's GPS position only updates when RemoteTerm hears an advertisement from that node that includes GPS coordinates. If the contact's device doesn't broadcast GPS or hasn't advertised recently, the tracker will show as unknown.

### Entity is "Unavailable"

Radio health entities have a 120-second expiry. If RemoteTerm stops sending health updates (e.g. it's shut down or loses connection to the broker), HA marks the entities as unavailable after 2 minutes. Restart RemoteTerm or check the broker connection.

## Removing the Integration

Disabling or deleting the HA integration in RemoteTerm's settings publishes empty retained messages to all discovery topics, which removes the devices and entities from HA automatically.

## Local Test Environment

For local development, RemoteTerm includes a helper that starts Mosquitto and Home Assistant with MQTT preconfigured:

```bash
./scripts/setup/start_ha_test_env.sh
```

That gives you:

- Home Assistant at `http://localhost:8123`
- Mosquitto at `localhost:1883`
- A pre-created HA MQTT integration using that broker

To watch all MQTT traffic during testing:

```bash
docker exec ha-test-mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -v
```

To stop and clean up:

```bash
./scripts/setup/stop_ha_test_env.sh --clean
```

## MQTT Topics Reference

Runtime/state topics (where data is published):

| Topic | Content | Update frequency |
|-------|---------|-----------------|
| `meshcore/{node_id}/health` | `{"connected": bool, "noise_floor_dbm": int}` | Every 60s |
| `meshcore/{node_id}/telemetry` | `{"battery_volts": float, ...}` | ~8h or manual |
| `meshcore/{node_id}/gps` | `{"latitude": float, "longitude": float, ...}` | On advert |
| `meshcore/{node_id}/events/message` | `{"event_type": "message_received", ...}` | On message |

Discovery topics (entity registration, under `homeassistant/`):

| Pattern | Entity type |
|---------|------------|
| `homeassistant/binary_sensor/meshcore_<node_id>/connected/config` | Radio connectivity |
| `homeassistant/sensor/meshcore_<node_id>/noise_floor/config` | Noise floor sensor |
| `homeassistant/sensor/meshcore_<node_id>/battery_voltage/config` | Repeater battery |
| `homeassistant/sensor/meshcore_<node_id>/*/config` | Other repeater sensors |
| `homeassistant/device_tracker/meshcore_<node_id>/config` | Contact GPS tracker |
| `homeassistant/event/meshcore_<node_id>/messages/config` | Message event entity |

The `{node_id}` is always the first 12 characters of the node's public key, lowercased.
