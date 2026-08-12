# ClockworkPi uConsole deployment (HackerGadgets AIO v2)

RemoteTerm can drive the SX1262 LoRa module on the [HackerGadgets AIO v2](https://hackergadgets.com/products/uconsole-aio-v2) expansion board directly over **SPI1**, turning a uConsole into a self-contained MeshCore node — no external radio needed. This board has real quirks that aren't obvious from the hardware alone; this doc captures what actually works.

**Tested on:** ClockworkPi uConsole, CM4 module, DragonOS (Debian Trixie-based), HackerGadgets AIO v2 board.

## Prerequisites

- uConsole with a CM4 module (this doc doesn't cover CM3/CM5/RISC-V variants)
- HackerGadgets AIO v2 board installed
- Antenna connected to the AIO v2's LoRa u.FL connector — obvious in hindsight, but worth stating: a loose or missing antenna produces the exact same symptoms as the SPI/config issues below (chip inits fine, TX/CAD fails), so rule it out early.

## 1. Enable SPI1

Edit `/boot/firmware/config.txt` and add **one line** under the `[all]` section:

```ini
[all]
dtoverlay=spi1-1cs
```

**Do not add `dtparam=spi=on`.** On uConsole this is known to blank the internal display (it conflicts with the backlight, which shares GPIO18 — the same pin as SPI1's hardware CE0). The `spi1-1cs` overlay alone is sufficient; RemoteTerm only opens `/dev/spidev1.0` and doesn't need the global SPI0 enable. If you ever do need `dtparam=spi=on` for something else, install `clockworkpi-backlight` first to resolve the conflict.

If you're on a Rex Bookworm-based image, `devterm-printer.service` also uses SPI1 and will conflict:

```bash
sudo systemctl disable devterm-printer.service
```

Reboot after this change, then confirm the bus exists:

```bash
ls /dev/spidev1.*
```

## 2. Power the LoRa module — and keep it powered on boot

The AIO v2's LoRa module is powered off by default to save battery. It's gated by GPIO16, managed by [`aiov2_ctl`](https://github.com/hackergadgets/aiov2_ctl):

```bash
git clone https://github.com/hackergadgets/aiov2_ctl.git
cd aiov2_ctl
sudo python3 ./aiov2_ctl.py --install
```

Set the LoRa rail to persist across reboots — this is what a systemd service (`aiov2-rails-boot.service`, installed above) applies at every boot:

```bash
sudo aiov2_ctl --boot-rail LORA on
aiov2_ctl --boot-rails-status   # confirm
```

**Wire the boot ordering into RemoteTerm's systemd unit.** Without this, on a cold boot there's no guarantee GPIO16 is high before `remoteterm.service` starts trying to talk to the radio:

```bash
sudo systemctl edit remoteterm.service
```

```ini
[Unit]
After=aiov2-rails-boot.service
Wants=aiov2-rails-boot.service
```

## 3. `meshtasticd` conflict — pick one radio driver, not both

If your image includes the HackerGadgets AIO companion apps (`meshtastic-mui` / `meshtasticd`), **it and RemoteTerm cannot both use the SPI1 bus at the same time.** Both will fail in confusing ways if they contend for it — you'll see `GPIO conflict — pin already in use by another process` loops in RemoteTerm's logs that never resolve, and `meshtasticd` can crash outright (`SX126x init result -2`, `No sx1262 radio`).

If you're running RemoteTerm as your primary driver for this radio, disable `meshtasticd` for good:

```bash
sudo systemctl stop meshtasticd.service
sudo systemctl mask meshtasticd.service
```

`meshtasticd.service` ships with `Restart=always`, so a plain `kill` on its process will just trigger a respawn — always use `systemctl stop`, and `mask` (not just `disable`) if you want a hard guarantee nothing brings it back (some AIO tooling can relaunch it outside of systemd's normal boot path).

To switch back to Meshtastic later: `sudo systemctl unmask meshtasticd.service`.

## 4. Install RemoteTerm

Same production install as any Pi — see [PI_DEPLOYMENT.md](PI_DEPLOYMENT.md) for the full walkthrough (`manage_remoterm.sh`, or the manual `/opt/remoteterm` + systemd steps). The uConsole-specific parts are all above and below this section.

If you'd rather not build the frontend on-device (no Node needed that way), build `frontend/dist` on your dev machine and `rsync` it over before installing — `install_remoteterm_pi.sh` / the manual install both skip the frontend build step if `frontend/dist/index.html` already exists.

## 5. Hardware profile

In the SPI setup wizard (`uv run python -m app.setup_cli`) or `data/config.yaml`, select the **`uconsole`** hardware profile:

```yaml
hardware:
  profile: "uconsole"
```

This profile (`app/backends/spi_config.py`) is pre-configured for the AIO v2 board:

| Setting | Value | Why |
|---|---|---|
| `bus_id` | 1 | SPI1, not SPI0 like most other supported boards |
| `cs_pin` | -1 | Use native hardware CS0, not a manual GPIO override |
| `reset_pin` / `busy_pin` / `irq_pin` | 25 / 24 / 26 | Confirmed against HackerGadgets' own setup guide |
| `use_dio3_tcxo` | `true` | **Required.** Supplies voltage to the TCXO via DIO3 |
| `use_dio2_rf` | `true` | **Required.** Uses DIO2 as the RF antenna switch control |

The `use_dio3_tcxo`/`use_dio2_rf` flags are not optional tuning — without them the chip responds to basic SPI config commands at boot (so it *looks* like it's working) but the RF frontend and TCXO clock reference are unstable, and every CAD/TX attempt fails (IRQ status reads return `0xFFFF`, no `TX_DONE` interrupt, transmissions silently never leave the antenna). See `pyMC_Repeater` PR [rightup/pyMC_Repeater#99](https://github.com/rightup/pyMC_Repeater/pull/99) and the [ClockworkPi forum thread](https://forum.clockworkpi.com/t/uconsole-is-very-cool-for-meshcore-exploration/21492) this profile is sourced from.

## 6. `pymc_core` version

The `spi` extra must resolve to **`pymc_core[hardware]>=1.0.12`** (or later). Versions before `1.0.10` have a hardcoded SPI1 chip-select workaround for "ClockworkPi/Heltec boards" that corrupts SPI reads on this specific board — the exact `0xFFFF`/TX-failure symptom described above, from a different cause than the DIO2/DIO3 setting. `1.0.8`/`1.0.9` don't exist on PyPI, so any range like `>=1.0.7,<1.0.10` silently pins to the broken `1.0.7`. This repo's `pyproject.toml` already requires `>=1.0.12`; if you're on an older clone, `uv sync --extra spi` after pulling `main` to pick up the fix.

## Troubleshooting

**`GPIO conflict — pin already in use by another process`, retrying every 5s indefinitely at boot:**
Something else is holding SPI1 or GPIO24/25/26. Check for `meshtasticd` first (see step 3) — `ps aux | grep -i meshtast` and `sudo lsof /dev/spidev1.0`. A handful of retries (3-6, ~15-30s) that then succeed is normal self-cleanup on boot; retries that never stop mean genuine contention.

**`SX1262_wrapper - ERROR - Critical: Radio reporting all interrupt flags set` / `IRQ status read returned 0xFFFF` / `TX completion timeout - no interrupt received!`:**
Work through, in order:
1. Antenna actually seated (reseat the u.FL connector)
2. `pymc_core >= 1.0.12` (step 6)
3. `use_dio3_tcxo` / `use_dio2_rf` both `true` on the `uconsole` profile (step 5)
4. Nothing else (namely `meshtasticd`) is holding the SPI1 bus (step 3)

If a completely independent LoRa stack (e.g. `meshtasticd`) *also* fails to initialize the same chip with the bus otherwise uncontended, that points to a hardware fault rather than anything in RemoteTerm or `pymc_core` — but rule out contention and the config flags above first, since they produce identical-looking symptoms and are far more common than a dead chip.

**`aiov2_ctl --status` shows `LORA GPIO16: ON` but RemoteTerm still can't talk to the radio:**
The rail being on only means the module is powered — it doesn't rule out bus contention (step 3) or the profile/version issues above (steps 5–6).

**Frontend page goes stale after a service restart (WebSocket reconnects but API calls silently do nothing):**
Hard-refresh the page (Ctrl/Cmd+Shift+R). This is a browser-side state issue, not a backend one — the WebSocket auto-reconnecting doesn't imply the rest of the page's state is still valid.
