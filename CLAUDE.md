**For detailed component documentation, see:**

- `./AGENTS.md` (general project information)
- `app/AGENTS.md` - Backend (FastAPI, database, radio connection, packet decryption)
- `frontend/AGENTS.md` - Frontend (React, state management, WebSocket, components)

## Testing a Branch on the Pi

**Target:** `tesso@meshcore-pi-companion.local` (192.168.1.188)
**Repo path on Pi:** `~/meshcore-pi-companion/`

### Hardware & Radio Config

- **HAT:** Waveshare LoRa HAT (SPI)
- **Preset:** USA/Canada (910.525 MHz, SF7, BW62.5, CR5)
- **Node name:** `T35t-D3v-Pi`
- **Config file:** `data/dev-pi-config.yaml` (checked into repo)
- **Identity:** The `identity_key` in `data/dev-pi-config.yaml` must be preserved across deploys so the node keeps the same mesh identity. On first-ever run, the app generates it — copy the value from the Pi's `data/config.yaml` back into `data/dev-pi-config.yaml` and commit it.

### Deploy

```bash
# 1. Push the branch to origin (from remote-terminal-fork/)
git push -u origin <branch-name>

# 2. Clone on Pi (first time) or fetch+checkout (if repo exists)
ssh tesso@192.168.1.188 "git clone -b <branch-name> https://github.com/codemonkeybr/meshcore-pi-companion.git ~/meshcore-pi-companion"
# OR if repo already exists:
ssh tesso@192.168.1.188 "cd ~/meshcore-pi-companion && git fetch origin && git checkout <branch-name> && git pull"

# 3. Send pre-built frontend (built locally in frontend/dist/)
rsync -a --delete remote-terminal-fork/frontend/dist/ tesso@192.168.1.188:~/meshcore-pi-companion/frontend/dist/

# 4. Install deps (use --verbose to diagnose failures)
ssh tesso@192.168.1.188 "cd ~/meshcore-pi-companion && ./scripts/install_remoteterm_pi.sh --verbose"

# 4b. Install local pyMC_core (PyPI version lacks send_group_text and other recent additions)
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='.venv' pyMC_core/ tesso@192.168.1.188:~/pyMC_core/
ssh tesso@192.168.1.188 "cd ~/meshcore-pi-companion && .venv/bin/pip install --no-cache-dir ~/pyMC_core/'[hardware]'"

# 5. Deploy the dev config (always use the stable dev identity)
ssh tesso@192.168.1.188 "mkdir -p ~/meshcore-pi-companion/data && cp ~/meshcore-pi-companion/data/dev-pi-config.yaml ~/meshcore-pi-companion/data/config.yaml"

# 6. Start the app
ssh tesso@192.168.1.188 "cd ~/meshcore-pi-companion && ./scripts/run_remoteterm.sh --host 0.0.0.0 --port 8000"
```

App is served at: `http://192.168.1.188:8000/`

### Cleanup (after testing)

```bash
# Stop the app (if running in foreground, Ctrl+C; if backgrounded:)
ssh tesso@192.168.1.188 "pkill -f 'uvicorn app.main:app'"

# Remove the repo and venv from the Pi
ssh tesso@192.168.1.188 "rm -rf ~/meshcore-pi-companion ~/pyMC_core"

# Optionally delete the remote branch
git push origin --delete <branch-name>
```
