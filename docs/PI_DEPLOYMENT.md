# Raspberry Pi deployment (SPI mode)

Short guide to run RemoteTerm on a Raspberry Pi with a LoRa HAT (e.g. Waveshare SX1262) using the **SPI backend** — no external MeshCore radio required.

## Prerequisites

- Raspberry Pi (3/4/Zero 2 W or similar) with Raspberry Pi OS
- LoRa HAT supported by the SPI backend (Waveshare, uConsole, PiMesh-1W, meshadv, HT-RA62, etc.)
- **SPI enabled:** `sudo raspi-config` → Interface Options → SPI → Enable, then reboot
- Python 3.10+ and (optional) Node.js if you build the frontend on the Pi

## 1. Install and configure

From the project root on the Pi (or a machine that will copy the tree to the Pi):

```bash
chmod +x scripts/install_remoterm_pi.sh
./scripts/install_remoterm_pi.sh
```

This script:

- Creates a virtualenv (`.venv`) if missing and installs backend deps with `.[spi]`
- If `config.yaml` does not exist, runs the **SPI setup wizard** to create it (node name, hardware profile, radio preset, location)
- Optionally builds the frontend if `npm` is available (can be skipped and `frontend/dist` copied from elsewhere to save memory)

To **only** run the SPI config wizard (e.g. to change node name or region later):

```bash
./scripts/install_remoterm_pi.sh --spi-config
```

Config is stored in **`config.yaml`** in the project root (or `data/config.yaml`). Copy from `config.yaml.example` if you prefer to edit by hand; the wizard writes the same structure.

## 2. Run the server

From the project root:

```bash
./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000
```

Or manually with the venv:

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://\<pi-ip\>:8000** in a browser (e.g. `http://192.168.1.191:8000`).

## 3. Identity and data

- **Identity:** On first run with SPI, an Ed25519 key is generated and stored under `data/` (via `app/spi_identity`). This is your node’s mesh identity; back up `data/` if you need to restore the same node.
- **Database:** SQLite is at `data/meshcore.db`; contacts, channels, and messages are stored there.

## 4. Troubleshooting

- **SPI not found / permission denied:** Ensure SPI is enabled and the process has access to `/dev/spidev0.*` (or `spidev1.*` for uConsole). Run as the same user that will run the app.
- **Wizard not starting:** Ensure `config.yaml` is missing so the install script runs the wizard, or run `./scripts/install_remoterm_pi.sh --spi-config` explicitly.
- **No presets in wizard:** Presets are fetched from `https://api.meshcore.nz/api/v1/config`; if offline, the app uses `data/radio-presets-fallback.json` (bundled in the repo).
- **Frontend not loading:** If `frontend/dist` is missing, build it with `cd frontend && npm install && npm run build` (or copy a prebuilt `dist` from another machine). The API and `/docs` still work without the frontend.

## 5. Optional: run as a service

Use the same systemd approach as in the main README (Systemd Service): install under e.g. `/opt/remoteterm`, point the service at the venv and set `WorkingDirectory` to the project root. SPI mode is selected automatically when `config.yaml` (or `data/config.yaml`) exists; no extra env vars are required for transport.
