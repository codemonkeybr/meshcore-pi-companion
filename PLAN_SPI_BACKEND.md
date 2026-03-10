# Plan: Add Direct SPI Radio Backend to Remote-Terminal

## Goal

Fork Remote-Terminal-for-MeshCore to support **direct SPI communication** with
LoRa radio hardware (Waveshare SX1262 HAT and others) via `pymc_core`, in
addition to the existing Serial/TCP/BLE transports that use the `meshcore`
client library.

This turns a Raspberry Pi + LoRa HAT into a self-contained MeshCore node with
the full Remote-Terminal web UI — no external radio device required.

## Reference Projects

| Directory (local) | Repo | Role |
|---|---|---|
| `remote-terminal-fork/` | jkingsman/Remote-Terminal-for-MeshCore | **Our fork** — the project we're modifying |
| `pyMC_core/` | rightup/pyMC_core | Reference — Python MeshCore implementation with SPI radio drivers |
| `pyMC_Repeater/` | rightup/pyMC_Repeater | Reference — shows how pyMC_core is used in a real app |

## Current Architecture

```
Remote-Terminal backend
  └── RadioManager (app/radio.py)
        └── meshcore library (PyPI: meshcore==2.2.29)
              └── Serial / TCP / BLE → external radio with C++ firmware
```

The `meshcore` library is a **client** that sends commands to an external
MeshCore radio device. The radio firmware handles mesh routing, packet
construction, cryptography, and RF transmission.

## Target Architecture

```
Remote-Terminal backend
  └── RadioManager (app/radio.py)
        └── RadioBackend (ABC)
              ├── ClientBackend (wraps meshcore library)
              │     └── Serial / TCP / BLE → external radio
              └── SpiBackend (wraps pymc_core)
                    └── SX1262Radio via SPI/GPIO → direct LoRa hardware
```

Backend is selected at startup via environment variables. Only one backend
is active at a time (same mutual-exclusivity as current transports).

---

## Phase 0: Preparation & Understanding

- [x] Clone all three projects
- [x] Map the full `meshcore` API surface used by Remote-Terminal
- [x] Study pymc_core's MeshNode API and Dispatcher
- [x] Study pyMC_Repeater for real-world pymc_core usage patterns
- [x] Document the gap between the two library APIs
- [x] Verify existing tests pass on the fork (`./scripts/all_quality.sh`)

## Phase 1: Define the RadioBackend Abstraction ✅ COMPLETE

**Goal:** Extract an abstract interface from the current `meshcore` usage
without changing any behavior. All existing code continues to work.

### 1.1 — Create `app/radio_backend.py`

Define an ABC `RadioBackend` with async methods covering every operation
Remote-Terminal needs. Group by function:

**Connection lifecycle:**
- `connect()` → establish connection
- `disconnect()` → clean disconnect
- `is_connected` → property
- `self_info` → property (node public key, name, etc.)
- `start_auto_message_fetching()` / `stop_auto_message_fetching()`

**Commands — Contacts:**
- `get_contacts()` → dict of contacts
- `add_contact(contact_dict)` → result
- `remove_contact(contact_data)` → result
- `get_contact_by_key_prefix(prefix)` → contact or None
- `evict_contact_from_cache(public_key)` → remove from internal state

**Commands — Channels:**
- `get_channel(idx)` → channel info
- `set_channel(idx, name, secret)` → result

**Commands — Messaging:**
- `send_msg(dst, msg, timestamp?)` → result with expected_ack
- `send_cmd(dst, cmd, timestamp?)` → result
- `send_chan_msg(channel_idx, msg, timestamp?)` → result
- `get_msg(timeout)` → result (pending message or no-more)

**Commands — Radio config:**
- `send_advert(flood)` → result
- `set_time(unix_ts)` → result
- `send_device_query()` → device info
- `set_name(name)` → result
- `set_coords(lat, lon)` → result
- `set_tx_power(val)` → result
- `set_radio(freq, bw, sf, cr)` → result
- `set_flood_scope(scope)` → result
- `reboot()` → None

**Commands — Security:**
- `export_private_key()` → key bytes
- `import_private_key(key)` → result

**Commands — Repeater operations:**
- `send_login(dst, pwd)` → result
- `send_logout(dst)` → result
- `send_statusreq(dst)` → result
- `req_status(contact, timeout)` → result
- `req_telemetry(contact, timeout)` → result
- `req_acl(contact, timeout)` → result
- `send_trace(auth_code, tag, flags, path)` → result

**Events:**
- `subscribe(event_type, handler)` → subscription
- `unsubscribe(subscription)` → None

**Internal (needed for specific workarounds):**
- `get_reader()` → reader object (for path_hash_mode frame capture)

### 1.2 — Create `app/backends/client_backend.py`

Implement `RadioBackend` by wrapping the existing `meshcore` library.
This is mostly a thin delegation layer — each method calls the
corresponding `meshcore` method and returns the result.

### 1.3 — Refactor `RadioManager` to use `RadioBackend`

Replace all direct `meshcore` library calls with calls through the
`RadioBackend` interface:

- `radio.py` — connection, properties, `radio_operation()` context manager
- `radio_sync.py` — contact/channel sync, polling, advertisements
- `event_handlers.py` — event subscriptions
- `routers/radio.py` — config, reboot, reconnect
- `routers/messages.py` — send DM, send channel msg, resend
- `routers/contacts.py` — add/remove from radio, commands, trace
- `routers/channels.py` — channel sync
- `routers/repeaters.py` — login, status, telemetry, etc.
- `packet_processor.py` — only indirectly (receives events)
- `keystore.py` — private key export

### 1.4 — Verify: all existing tests still pass

Run `./scripts/all_quality.sh` and fix anything that broke.

## Phase 2: Build the SPI Backend ✅ COMPLETE

**Goal:** Implement `RadioBackend` using `pymc_core`, enabling direct
SPI radio communication.

### 2.1 — Add `pymc_core[hardware]` as a dependency

Add to `pyproject.toml` as an optional dependency group:

```toml
[project.optional-dependencies]
spi = ["pymc_core[hardware]"]
```

### 2.2 — Create `app/backends/spi_backend.py`

Implement `RadioBackend` using `pymc_core.MeshNode`:

**Key adapter decisions:**

| meshcore concept | SPI backend equivalent |
|---|---|
| `create_serial/tcp/ble` | `SX1262Radio(bus, cs, reset, busy, irq, ...)` + `MeshNode(radio, identity, ...)` |
| `mc.commands.get_contacts()` | Read from DB (contacts are local, not on a remote device) |
| `mc.commands.add_contact()` | Write to local contact store |
| `mc.commands.get_channel(idx)` | Read from DB |
| `mc.commands.send_msg()` | `node.send_text(contact_name, msg)` |
| `mc.commands.send_chan_msg()` | `node.send_group_text(group_name, msg)` |
| `mc.commands.send_advert()` | Build and send advert packet via Dispatcher |
| `mc.commands.set_time()` | No-op (we ARE the clock) |
| `mc.commands.send_device_query()` | Return local config dict |
| `mc.commands.export_private_key()` | Return `LocalIdentity` private key directly |
| `mc.commands.reboot()` | Restart Dispatcher + reinit SPI radio |
| `mc.self_info` | Constructed from local identity + config |
| `mc.is_connected` | `True` if SPI radio initialized |
| Auto-fetch / get_msg() | No-op (Dispatcher handles incoming directly) |
| EventType subscriptions | Translate Dispatcher callbacks → compatible events |
| RX_LOG_DATA | Tap Dispatcher packet reception to emit raw bytes |

**Critical: Raw packet pipeline integration**

Remote-Terminal's `packet_processor.py` is the heart of incoming data. It
receives raw bytes via `RX_LOG_DATA` events and handles:
- Channel message decryption
- DM decryption
- Advertisement processing
- Deduplication
- Database storage
- WebSocket broadcast

For the SPI backend, we need to intercept packets in pymc_core's Dispatcher
*before* it processes them and also emit them as raw bytes to the packet
processor. This might mean:
1. A custom Dispatcher subclass, or
2. Hooking into Dispatcher's packet reception callback, or
3. Using pymc_core's event_service to forward raw packets

**Contact/channel storage adapters:**

pymc_core expects injected `contacts` and `channel_db` objects. We create
thin adapters that delegate to Remote-Terminal's existing repositories:
- `SpiContactStore` — wraps `ContactRepository`
- `SpiChannelDB` — wraps `ChannelRepository`

### 2.3 — Create `app/backends/spi_config.py`

Hardware configuration for supported boards, sourced from pyMC_Repeater's
`radio-settings.json` and pymc_core's documented configs:

```python
HARDWARE_PROFILES = {
    "waveshare": {
        "name": "Waveshare LoRa HAT",
        "bus_id": 0, "cs_id": 0, "cs_pin": 21,
        "reset_pin": 18, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": 13, "rxen_pin": 12,
        "is_waveshare": True,
        "notes": "SPI version only. UART version not supported.",
        "prerequisites": ["Enable SPI via raspi-config"],
    },
    "uconsole": {
        "name": "uConsole LoRa Module (HackerGadgets All-In-One Board)",
        "bus_id": 1, "cs_id": 0, "cs_pin": -1,
        "reset_pin": 25, "busy_pin": 24, "irq_pin": 26,
        "txen_pin": -1, "rxen_pin": -1,
        "notes": "Uses SPI1. Has built-in GPS/RTC.",
        "prerequisites": [
            "Enable SPI and SPI1 overlay: add 'dtparam=spi=on' and "
            "'dtoverlay=spi1-1cs' to /boot/firmware/config.txt",
            "Disable devterm-printer service if on Rex Bookworm: "
            "'sudo systemctl disable devterm-printer.service'",
            "Reboot after config changes",
        ],
        "has_gps": True,
        "has_rtc": True,
    },
    "pimesh-1w-usa": {
        "name": "PiMesh-1W (USA)",
        "bus_id": 0, "cs_id": 0, "cs_pin": 21,
        "reset_pin": 18, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": 13, "rxen_pin": 12,
        "use_dio3_tcxo": True,
        "default_tx_power": 30,
        "prerequisites": ["Enable SPI via raspi-config"],
    },
    "pimesh-1w-uk": {
        "name": "PiMesh-1W (UK)",
        "bus_id": 0, "cs_id": 0, "cs_pin": 21,
        "reset_pin": 18, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": 13, "rxen_pin": 12,
        "use_dio3_tcxo": True,
        "prerequisites": ["Enable SPI via raspi-config"],
    },
    "meshadv-mini": {
        "name": "FrequencyLabs meshadv-mini",
        "bus_id": 0, "cs_id": 0, "cs_pin": 8,
        "reset_pin": 24, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": -1, "rxen_pin": 12,
        "prerequisites": ["Enable SPI via raspi-config"],
    },
    "meshadv": {
        "name": "FrequencyLabs meshadv",
        "bus_id": 0, "cs_id": 0, "cs_pin": 21,
        "reset_pin": 18, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": 13, "rxen_pin": 12,
        "use_dio3_tcxo": True,
        "prerequisites": ["Enable SPI via raspi-config"],
    },
    "ht-ra62": {
        "name": "Heltec HT-RA62 Module",
        "bus_id": 0, "cs_id": 0, "cs_pin": 21,
        "reset_pin": 18, "busy_pin": 20, "irq_pin": 16,
        "txen_pin": -1, "rxen_pin": -1,
        "use_dio3_tcxo": True, "use_dio2_rf": True,
        "prerequisites": ["Enable SPI via raspi-config"],
    },
}
```

### uConsole-Specific Considerations

The Clockwork uConsole is a special case among supported hardware:

**SPI1 requirement:**
The uConsole's LoRa module uses SPI bus 1 (all other boards use SPI bus 0).
This requires adding `dtoverlay=spi1-1cs` to `/boot/firmware/config.txt`.
The pymc_core SX126x driver has a built-in workaround for SPI1 CS0 — it
uses GPIO 17 instead of GPIO 18 because pin 18 is occupied on ClockworkPi.

**Service conflict:**
The `devterm-printer.service` (present on Rex Bookworm images) conflicts
with SPI and must be disabled before the radio can initialize.

**Built-in GPS:**
The uConsole's all-in-one board includes GPS hardware. Future enhancement:
read GPS coordinates automatically for location advertisements instead of
requiring manual lat/lon entry. This would need:
- A GPS reader module (e.g. `gpsd` integration or direct serial read)
- Auto-populate location in node config
- Periodic location updates in advertisements

**Built-in RTC:**
The RTC means the uConsole can keep accurate time without NTP. In SPI mode,
`set_time()` is a local operation, so the RTC makes this reliable even
when the Pi is offline.

**Portable terminal use case:**
The uConsole has a screen, keyboard, and battery — it's a complete portable
mesh terminal. Users will likely run the browser directly on the uConsole
(`http://localhost:8000`) while also being accessible remotely. The setup
wizard should detect if it's running on a uConsole and offer to auto-start
Chromium in kiosk mode as a future enhancement.

**Setup wizard: prerequisites check**
The setup wizard should display hardware-specific prerequisites when the
user selects uConsole, and ideally verify:
- SPI1 is available (`ls /dev/spidev1.*`)
- devterm-printer is not running
- GPS device is accessible (optional)
```

### 2.4 — Update `app/config.py`

Add new environment variables:

| Variable | Default | Description |
|---|---|---|
| `MESHCORE_SPI_PROFILE` | *(none)* | Hardware profile name (e.g. "waveshare") |
| `MESHCORE_SPI_FREQUENCY` | `869525000` | LoRa frequency in Hz |
| `MESHCORE_SPI_TX_POWER` | `22` | TX power in dBm |
| `MESHCORE_SPI_BUS` | *(from profile)* | Override SPI bus ID |
| `MESHCORE_SPI_CS` | *(from profile)* | Override CS pin |
| `MESHCORE_SPI_RESET` | *(from profile)* | Override reset pin |
| `MESHCORE_SPI_BUSY` | *(from profile)* | Override busy pin |
| `MESHCORE_SPI_IRQ` | *(from profile)* | Override IRQ pin |
| `MESHCORE_NODE_NAME` | `"RemoteTerm"` | Node name for SPI mode |

When `MESHCORE_SPI_PROFILE` is set, SPI transport is selected. Mutually
exclusive with serial/TCP/BLE.

### 2.5 — Update `RadioManager.connect()` dispatch

Add SPI as a transport option:

```python
connection_type = settings.connection_type  # now includes "spi"
if connection_type == "spi":
    await self._connect_spi()
elif connection_type == "tcp":
    ...
```

## Phase 2.5: First-Boot Provisioning (SPI Mode)

**Goal:** When SPI mode is selected but no identity/config exists, guide the
user through initial setup — just like flashing a new MeshCore device.

### Why this is needed

With Serial/TCP/BLE, the external radio is already provisioned: firmware
flashed, identity generated, region configured. With SPI, the Pi starts as a
blank slate. Without provisioning:
- No Ed25519 identity → can't sign/encrypt anything
- No frequency config → can't talk to other nodes on the mesh
- No node name → shows as "unknown" on the network

### What pyMC_Repeater does (for reference)

1. **Identity:** `_load_or_create_identity_key()` generates a 32-byte random
   Ed25519 private key, persists to `~/.config/pymc_repeater/identity.key`
   (base64-encoded, mode 0600). Loaded on every subsequent boot.

2. **Setup wizard:** `setup-radio-config.sh` is an interactive bash script:
   - Prompts for node name
   - Selects hardware profile from `radio-settings.json`
   - Fetches radio presets from `https://api.meshcore.nz/api/v1/config`
     (falls back to local `radio-presets.json`)
   - User picks region → sets frequency, SF, BW, CR
   - Writes everything to `config.yaml`

3. **Radio init:** `get_radio_for_board()` reads config and creates
   `SX1262Radio` instance with all pin mappings + radio parameters.

### Our approach: Web-based setup wizard + CLI fallback

Since Remote-Terminal is already a web app, the first-boot experience should
be web-based too. The flow:

**Startup behavior when SPI config is missing/incomplete:**
1. Server starts in "setup mode" — API is available, frontend loads
2. Frontend detects unconfigured state via `GET /api/health` (new field:
   `setup_required: true`)
3. Frontend shows a setup wizard instead of the normal UI
4. Wizard steps:
   a. **Node name** — text input, default generated (e.g. "RemoteTerm-XXXX")
   b. **Hardware profile** — dropdown of supported boards (Waveshare, uConsole,
      meshadv-mini, meshadv, HT-RA62) with pin diagrams/descriptions
   c. **Region / radio preset** — fetched from `api.meshcore.nz` or local
      fallback. Shows frequency, SF, BW, CR for each option
   d. **GPS coordinates** (optional) — lat/lon for location advertisements
   e. **Review & confirm**
5. `POST /api/setup/provision` saves config and generates identity
6. Server restarts SPI backend with new config, transitions to normal mode

**What gets persisted (new file: `data/spi_config.json`):**
```json
{
  "node_name": "RemoteTerm-4829",
  "identity_key": "base64-encoded-32-byte-private-key",
  "hardware_profile": "waveshare",
  "hardware_overrides": {},
  "radio": {
    "frequency": 869525000,
    "bandwidth": 62500,
    "spreading_factor": 8,
    "coding_rate": 8,
    "tx_power": 22,
    "preamble_length": 17,
    "sync_word": 13380
  },
  "location": {
    "latitude": 0.0,
    "longitude": 0.0
  }
}
```

**Why `data/spi_config.json` and not env vars or `config.yaml`:**
- Env vars can't be modified at runtime (can't provision from the web UI)
- JSON is native to the Python/FastAPI stack (no PyYAML dependency needed)
- Lives in `data/` alongside the SQLite DB — same backup/persistence story
- Hardware-specific pin overrides can still come from env vars for advanced
  users (the env vars in Phase 2.4 serve as overrides)

**CLI fallback:** For headless setup without a browser, a simple script:
```bash
python -m app.setup_cli
```
Prompts interactively for the same info and writes `data/spi_config.json`.

### Identity management specifics

- Private key: 32-byte Ed25519 seed, generated via `os.urandom(32)`
- Stored in `data/spi_config.json` (base64-encoded)
- Public key derived on startup via `pymc_core.LocalIdentity(private_key)`
- The key IS the node's mesh identity — changing it means becoming a
  different node on the network
- Key export: `export_private_key()` on the SPI backend returns this
  key directly (no radio firmware to query)
- Key import: `import_private_key()` replaces the stored key and
  restarts the node with the new identity

### Radio preset source

Reuse pyMC_Repeater's approach: fetch from `https://api.meshcore.nz/api/v1/config`
at setup time, with a bundled fallback JSON. Remote-Terminal's frontend already
has radio presets in `frontend/src/utils/radioPresets.ts` — we should align
with or extend those for the setup wizard.

### New files for this phase

- `app/routers/setup.py` — provisioning API endpoints
- `app/setup_cli.py` — CLI provisioning script
- `app/spi_identity.py` — identity generation/loading
- `frontend/src/components/SetupWizard.tsx` — setup wizard UI
- `data/spi_config.json` — generated at provisioning time

### New API endpoints

- `GET /api/setup/status` — is provisioning needed?
- `GET /api/setup/hardware-profiles` — list supported boards
- `GET /api/setup/radio-presets` — fetch region/frequency options
- `POST /api/setup/provision` — save config + generate identity

## Phase 3: Testing & Integration

### 3.1 — Unit tests for the abstraction layer

- Test `ClientBackend` wraps meshcore correctly (mock meshcore)
- Test `SpiBackend` wraps pymc_core correctly (mock SX1262Radio)
- Test `RadioManager` works with either backend

### 3.2 — Integration tests

- Test the full packet pipeline with SPI backend (mock radio, real DB)
- Test contact/channel sync flows
- Test message send/receive roundtrip

### 3.3 — Hardware testing

- Test on actual Raspberry Pi + Waveshare HAT
- Verify DM send/receive, channel messages, advertisements
- Verify WebSocket events propagate correctly to frontend
- Test reconnection behavior (SPI bus errors, etc.)

## Phase 4: Cleanup & Polish

### 4.1 — Update documentation

- Update `README.md` with SPI setup instructions
- Update `AGENTS.md` files with new architecture
- Add Pi-specific deployment guide

### 4.2 — Docker removal (optional)

Docker can be kept for the client-backend mode if desired. For SPI mode,
Docker doesn't make sense (needs direct GPIO/SPI access). Options:
- Keep Docker for serial/TCP mode only
- Remove entirely if the focus is Pi + SPI

### 4.3 — Configuration wizard (optional)

A CLI script similar to pyMC_Repeater's `setup-radio-config.sh` that:
- Detects available SPI devices
- Guides hardware profile selection
- Sets frequency for region (EU 868 / US 915)
- Generates `.env` file

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| pymc_core protocol gaps vs. C++ firmware | Messages not decoded by other nodes | Test interop with firmware nodes on a live mesh |
| pymc_core API changes (actively developed) | Breaking changes in updates | Pin version, contribute upstream if needed |
| Raw packet pipeline mismatch | Packet processor can't decode SPI packets | Ensure Dispatcher emits bytes in same format as meshcore RX_LOG_DATA |
| SPI on non-Pi hardware | Crashes on boot if no SPI bus | Graceful error, fall back to "no radio" state |
| Concurrent access to SPI | Corruption if two operations overlap | Reuse RadioManager's existing operation lock |
| Performance on Pi Zero/3 | Slow packet processing | Profile; pymc_core is designed for Pi-class hardware |

## Open Questions

1. **Should we run pymc_core's Dispatcher in its own asyncio task?**
   `node.start()` calls `dispatcher.run_forever()` which blocks. We need
   it running alongside FastAPI's event loop. Likely needs to be a
   background task.

2. **How to handle the "no radio slots" difference?**
   With meshcore, contacts are stored on the radio (limited to ~350).
   Remote-Terminal offloads them to DB and reloads as needed. With SPI,
   there are no radio slots — everything is in DB. The sync/offload cycle
   can be simplified or skipped entirely.

3. **What about firmware updates?**
   With an external radio, firmware is updated separately. With SPI +
   pymc_core, the "firmware" is the Python code. Updates come via
   `pip install --upgrade pymc_core`.

4. **Should the SPI backend support being a repeater?**
   pyMC_Repeater shows this is possible. Could be a future enhancement —
   the node acts as both a client (with web UI) and a repeater.

5. **Identity portability: can you move an identity from a flashed device?**
   If someone already has a MeshCore node with contacts/history, they may
   want to import that identity onto the Pi. The `import_private_key()`
   path handles this, but we should make it accessible from the setup
   wizard ("Import existing identity" option alongside "Generate new").

6. **Should radio presets be fetched live or bundled?**
   pyMC_Repeater fetches from `api.meshcore.nz` with a local fallback.
   Remote-Terminal's frontend already bundles presets in `radioPresets.ts`.
   We should support both: live fetch for latest presets, bundled fallback
   for offline/air-gapped Pi setups.

7. **Duty cycle enforcement?**
   pyMC_Repeater has duty cycle config (max airtime per minute). With a
   flashed radio, firmware enforces this. With SPI, we'd need to handle
   it in Python. pymc_core may already handle this — needs investigation.

---

## File Inventory: What Gets Created/Modified

### New files:
- `app/radio_backend.py` — ABC definition
- `app/backends/__init__.py`
- `app/backends/client_backend.py` — meshcore wrapper
- `app/backends/spi_backend.py` — pymc_core wrapper
- `app/backends/spi_config.py` — hardware profiles
- `app/backends/spi_contact_store.py` — contact adapter for pymc_core
- `app/backends/spi_channel_db.py` — channel adapter for pymc_core
- `app/routers/setup.py` — provisioning API endpoints
- `app/setup_cli.py` — CLI provisioning script (headless first-boot)
- `app/spi_identity.py` — identity generation, loading, import/export
- `frontend/src/components/SetupWizard.tsx` — web-based setup wizard
- `data/spi_config.json` — generated at provisioning time (not committed)
- `data/radio-presets-fallback.json` — bundled radio presets for offline setup
- `tests/test_radio_backend.py` — abstraction tests
- `tests/test_spi_backend.py` — SPI backend tests
- `tests/test_setup.py` — provisioning flow tests

### Modified files:
- `app/radio.py` — use RadioBackend instead of meshcore directly
- `app/radio_sync.py` — use RadioBackend methods
- `app/event_handlers.py` — subscribe through RadioBackend
- `app/config.py` — add SPI environment variables
- `app/main.py` — backend selection at startup
- `app/routers/radio.py` — use RadioBackend
- `app/routers/messages.py` — use RadioBackend
- `app/routers/contacts.py` — use RadioBackend
- `app/routers/channels.py` — use RadioBackend
- `app/routers/repeaters.py` — use RadioBackend
- `app/keystore.py` — use RadioBackend for key export
- `pyproject.toml` — add pymc_core optional dependency
- `AGENTS.md` — document new architecture
- `app/AGENTS.md` — document backend abstraction
- `README.md` — SPI setup instructions
