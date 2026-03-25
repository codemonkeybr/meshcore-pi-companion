# Raspberry Pi deployment (SPI or USB LoRa)

RemoteTerm on **Raspberry Pi OS** is best installed with the interactive manager script (same idea as pyMC Repeater’s `manage.sh`: whiptail menu, systemd, uninstall).

## Recommended: `scripts/manage_remoterm.sh`

From a clone of this repo on the Pi:

```bash
chmod +x scripts/manage_remoterm.sh
sudo ./scripts/manage_remoterm.sh
```

Or non-interactive:

```bash
sudo ./scripts/manage_remoterm.sh install
sudo ./scripts/manage_remoterm.sh help
```

**What it does**

- Targets **Raspberry Pi** only (override with `ALLOW_NON_PI=1` for debugging).
- **Install**: choose **SPI + LoRa HAT** (writes `data/config.yaml` via `python -m app.setup_cli`) or **USB serial** MeshCore radio (writes `/etc/remoterm/environment` with `MESHCORE_SERIAL_PORT`).
- Optionally enables **SPI** in `/boot/firmware/config.txt` or `/boot/config.txt` (`dtparam=spi=on`) and can reboot when needed.
- Copies the tree to `/opt/remoteterm`, creates user `remoteterm`, installs Python deps with `.[spi]`, tries to fetch the **prebuilt frontend** zip (see below), installs `remoteterm.service` under `/etc/systemd/system/`, runs **`systemctl enable`** (starts on boot), but does **not** `systemctl start` — use **Start** in the manager or `sudo systemctl start remoteterm` for the first run.
- **Upgrade** / **uninstall** / **logs** / **status** from the same script.

**Paths**

| Path | Purpose |
|------|---------|
| `/opt/remoteterm` | Application and `.venv` |
| `/var/lib/remoteterm` | Service user home (`remoteterm`); removed on uninstall (backup under `/tmp/remoteterm_varlib_backup_*` first). |
| `/etc/remoterm/environment` | Optional env for USB serial (see `remoteterm.service`) |
| `data/config.yaml` | SPI LoRa HAT config (default `MESHCORE_CONFIG_FILE`) |
| `data/meshcore.db` | SQLite (set in unit as `MESHCORE_DATABASE_PATH`) |

**Prebuilt frontend (no Node on the Pi)**

Default download URL (override with `FRONTEND_RELEASE_URL`):

`https://github.com/codemonkeybr/meshcore-pi-companion/releases/download/frontend-latest/frontend-dist.zip`

If download fails, place `frontend/frontend-dist.zip` next to the project or build `frontend/dist` on another machine and copy it.

**SPI setup wizard only**

After install, or to reconfigure:

```bash
sudo ./scripts/manage_remoterm.sh config-spi
```

Equivalent manual command from `/opt/remoteterm`:

```bash
cd /opt/remoteterm
sudo -u remoteterm env HOME=/var/lib/remoteterm PYTHONPATH=/opt/remoteterm \
  ./.venv/bin/python -m app.setup_cli --config-out data/config.yaml
```

**USB serial only**

```bash
sudo ./scripts/manage_remoterm.sh config-usb
```

Ensure no SPI `data/config.yaml` is present when using USB (the manager backs up and removes SPI configs when switching to USB).

**Setup API (optional)**

For automation or a future web wizard: `GET/POST /api/setup/*` — see `app/AGENTS.md`.

## Lightweight: `scripts/install_remoteterm_pi.sh`

Use this only for a **quick dev install** in a project root (venv + `pip install ".[spi]"` + optional local zip). It does **not** install systemd or SPI boot config. For production Pi images, use `manage_remoterm.sh` above.

## Run without systemd

```bash
./scripts/run_remoteterm.sh --host 0.0.0.0 --port 8000
```

Open `http://<pi-ip>:8000`.

## Identity and data

- **SPI:** On first run, an Ed25519 identity is created under `data/` (`app/spi_identity`). Back up `data/` to keep the same node.
- **Database:** `data/meshcore.db`.

## Troubleshooting

- **pip / PyPI timeouts (`RemoteDisconnected`, retries on `/simple/...`):** The install script retries with a longer timeout. Use a stable network, try again, or point pip at a mirror before running the manager, for example: `export PIP_INDEX_URL=https://pypi.org/simple/` or your org mirror. You can also raise the wait with `export PIP_DEFAULT_TIMEOUT=300`.
- **SPI not found / permission denied:** Enable SPI; ensure the process user can access `/dev/spidev*`. Add `remoteterm` to `spi` and `gpio` if needed: `sudo usermod -aG spi,gpio remoteterm`.
- **USB not found:** Set `MESHCORE_SERIAL_PORT` via `config-usb` or `/etc/remoterm/environment`.
- **Presets offline:** `data/radio-presets-fallback.json` is bundled.
- **SPI radio reboot:** After `POST /api/radio/reboot`, the driver may not release GPIO; restart the service if reconnection fails.

## systemd unit file

See [`remoteterm.service`](../remoteterm.service): `EnvironmentFile=-/etc/remoterm/environment` for USB overrides; `MESHCORE_DATABASE_PATH` for the SQLite file.
