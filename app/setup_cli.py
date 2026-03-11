"""Interactive CLI setup for SPI mode (Raspberry Pi + LoRa HAT).

This script helps you generate a `config.yaml` suitable for the SPI backend,
mirroring the UX of pyMC_Repeater's `setup-radio-config.sh` but tailored to
RemoteTerm's configuration format.

Usage (from project root):

    uv run python -m app.setup_cli

The wizard will:
  - Ask for a node name (default: existing value or a generated one)
  - Let you pick a hardware profile from `app.backends.spi_config.HARDWARE_PROFILES`
  - Fetch radio presets from https://api.meshcore.nz/api/v1/config, with a
    fallback to `data/radio-presets-fallback.json` if the network call fails
  - Write or update `config.yaml` in the current working directory
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any

from app.backends.spi_config import HARDWARE_PROFILES


API_URL = "https://api.meshcore.nz/api/v1/config"
FALLBACK_PRESETS_PATH = Path("data/radio-presets-fallback.json")
CONFIG_PATH = Path("config.yaml")


def _print_header() -> None:
    print("=== RemoteTerm SPI Radio Configuration ===")
    print()


def _load_existing_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - guarded by optional dependency
        print("ERROR: PyYAML is required for SPI setup. Install with:", file=sys.stderr)
        print("  uv add pyyaml  # or pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError:  # pragma: no cover - guarded by optional dependency
        print("ERROR: PyYAML is required for SPI setup. Install with:", file=sys.stderr)
        print("  uv add pyyaml  # or pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    with path.open("w") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    print(f"\nConfig written to {path.resolve()}")


def _prompt(text: str, default: str | None = None) -> str:
    if default:
        prompt_text = f"{text} [{default}]: "
    else:
        prompt_text = f"{text}: "
    value = input(prompt_text).strip()
    return value or (default or "")


def _step_node_name(config: dict[str, Any]) -> None:
    print("=== Step 0: Node Name ===\n")
    existing_name = (
        (config.get("node") or {}).get("name")
        if isinstance(config.get("node"), dict)
        else None
    )
    if existing_name:
        default_name = str(existing_name)
    else:
        default_name = f"RemoteTerm-{random.randint(0, 9999):04d}"

    name = _prompt("Enter node name", default=default_name)
    node_cfg = dict(config.get("node") or {})
    node_cfg["name"] = name
    config["node"] = node_cfg
    print(f"Node name: {name}\n")


def _step_hardware_profile(config: dict[str, Any]) -> None:
    print("=== Step 1: Hardware Profile ===\n")
    profiles = sorted(HARDWARE_PROFILES.items())
    for idx, (key, profile) in enumerate(profiles, start=1):
        description = getattr(profile, "name", key)
        print(f" {idx:2d}) {description} ({key})")
    print()

    current_profile = (
        (config.get("hardware") or {}).get("profile")
        if isinstance(config.get("hardware"), dict)
        else None
    )

    while True:
        default_index = None
        if current_profile:
            for idx, (key, _) in enumerate(profiles, start=1):
                if key == current_profile:
                    default_index = idx
                    break
        choice_raw = _prompt(
            "Select hardware profile number",
            default=str(default_index) if default_index is not None else None,
        )
        try:
            choice = int(choice_raw)
        except ValueError:
            print("Please enter a number.\n")
            continue
        if not 1 <= choice <= len(profiles):
            print(f"Please choose between 1 and {len(profiles)}.\n")
            continue
        key, profile = profiles[choice - 1]
        hw_cfg = dict(config.get("hardware") or {})
        hw_cfg["profile"] = key
        config["hardware"] = hw_cfg
        print(f"Selected hardware: {getattr(profile, 'name', key)} ({key})\n")
        break


def _fetch_radio_presets() -> list[dict[str, Any]]:
    """Fetch radio presets from the API, with local JSON fallback."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - guarded by optional dependency
        httpx = None

    presets: list[dict[str, Any]] = []

    if httpx is not None:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(API_URL)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    suggested = (data.get("config") or {}).get("suggested_radio_settings") or {}
                    entries = suggested.get("entries")
                    if isinstance(entries, list):
                        presets = entries
                elif isinstance(data, list):
                    presets = data
        except Exception:
            pass

    if not presets and FALLBACK_PRESETS_PATH.is_file():
        try:
            with FALLBACK_PRESETS_PATH.open() as f:
                data = json.load(f)
            if isinstance(data, list):
                presets = data
        except Exception:
            pass

    return presets


def _step_radio_settings(config: dict[str, Any]) -> None:
    print("=== Step 2: Radio Settings ===\n")
    presets = _fetch_radio_presets()
    if not presets:
        print(
            "WARNING: Could not fetch radio presets from API or fallback file.\n"
            "You can edit frequency and LoRa params manually in config.yaml.\n"
        )
        radio_cfg = dict(config.get("radio") or {})
        radio_cfg.setdefault("frequency", 910525000)
        radio_cfg.setdefault("tx_power", 22)
        radio_cfg.setdefault("bandwidth", 62500)
        radio_cfg.setdefault("spreading_factor", 7)
        radio_cfg.setdefault("coding_rate", 5)
        radio_cfg.setdefault("preamble_length", 17)
        radio_cfg.setdefault("sync_word", 13380)
        config["radio"] = radio_cfg
        return

    entries: list[tuple[str, dict[str, Any]]] = []
    for preset in presets:
        title = str(preset.get("title") or preset.get("name") or "Unnamed preset")
        entries.append((title, preset))

    for idx, (title, preset) in enumerate(entries, start=1):
        freq_mhz = float(preset.get("frequency", 0.0))
        sf = preset.get("spreading_factor")
        bw_khz = preset.get("bandwidth")
        cr = preset.get("coding_rate")
        print(
            f" {idx:2d}) {title:<35} "
            f"--> {freq_mhz:7.3f} MHz / SF{sf} / BW{bw_khz} / CR{cr}"
        )
    print()

    while True:
        choice_raw = _prompt("Select a radio preset number")
        try:
            choice = int(choice_raw)
        except ValueError:
            print("Please enter a number.\n")
            continue
        if not 1 <= choice <= len(entries):
            print(f"Please choose between 1 and {len(entries)}.\n")
            continue
        _, preset = entries[choice - 1]
        break

    freq_mhz = float(preset.get("frequency", 0.0))
    sf = int(preset.get("spreading_factor", 7))
    bw_khz = float(preset.get("bandwidth", 62.5))
    cr = int(preset.get("coding_rate", 5))

    freq_hz = int(freq_mhz * 1_000_000)
    bw_hz = int(bw_khz * 1_000)

    radio_cfg = dict(config.get("radio") or {})
    radio_cfg["frequency"] = freq_hz
    radio_cfg["tx_power"] = int(radio_cfg.get("tx_power", 22))
    radio_cfg["bandwidth"] = bw_hz
    radio_cfg["spreading_factor"] = sf
    radio_cfg["coding_rate"] = cr
    radio_cfg.setdefault("preamble_length", 17)
    radio_cfg.setdefault("sync_word", 13380)
    config["radio"] = radio_cfg

    print(
        f"\nSelected preset: {preset.get('title') or preset.get('name')}\n"
        f"  Frequency: {freq_mhz:.3f} MHz ({freq_hz} Hz)\n"
        f"  SF: {sf}, BW: {bw_khz} kHz ({bw_hz} Hz), CR: {cr}\n"
    )


def _step_location(config: dict[str, Any]) -> None:
    print("=== Step 3: Location (optional) ===\n")
    node_cfg = dict(config.get("node") or {})
    current_lat = node_cfg.get("latitude", 0.0)
    current_lon = node_cfg.get("longitude", 0.0)
    lat_raw = _prompt("Latitude (decimal, optional)", default=str(current_lat))
    lon_raw = _prompt("Longitude (decimal, optional)", default=str(current_lon))
    try:
        lat = float(lat_raw)
    except ValueError:
        lat = current_lat
    try:
        lon = float(lon_raw)
    except ValueError:
        lon = current_lon
    node_cfg["latitude"] = lat
    node_cfg["longitude"] = lon
    config["node"] = node_cfg
    print(f"Location set to lat={lat}, lon={lon}\n")


def _ensure_logging(config: dict[str, Any]) -> None:
    log_cfg = dict(config.get("logging") or {})
    log_cfg.setdefault("level", "INFO")
    config["logging"] = log_cfg


def main() -> int:
    _print_header()

    if not CONFIG_PATH.is_file():
        example = Path("config.yaml.example")
        if example.is_file():
            print(f"Base config not found, copying from {example}...")
            CONFIG_PATH.write_text(example.read_text())
        else:
            print(
                "Base config.yaml not found and config.yaml.example is missing.\n"
                "Please ensure you run this from the project root.",
                file=sys.stderr,
            )
            return 1

    config = _load_existing_config(CONFIG_PATH)

    _step_node_name(config)
    _step_hardware_profile(config)
    _step_radio_settings(config)
    _step_location(config)
    _ensure_logging(config)

    _save_config(CONFIG_PATH, config)

    node_name = (config.get("node") or {}).get("name", "")
    hw_profile = (config.get("hardware") or {}).get("profile", "")
    radio_cfg = config.get("radio") or {}
    freq = radio_cfg.get("frequency")
    sf = radio_cfg.get("spreading_factor")
    bw = radio_cfg.get("bandwidth")
    cr = radio_cfg.get("coding_rate")

    print("\nApplied configuration summary:")
    print(f"  Node name: {node_name}")
    print(f"  Hardware:  {hw_profile}")
    print(f"  Frequency: {freq} Hz")
    print(f"  SF/BW/CR:  SF{sf} / BW{bw} / CR{cr}")

    print(
        "\nNext steps:\n"
        "  - Enable SPI via `sudo raspi-config` → Interface Options → SPI\n"
        "  - Install SPI extras: `uv add 'pymc_core[hardware]' pyyaml httpx` "
        "  (or use the [spi] extra defined in pyproject.toml)\n"
        "  - Start RemoteTerm with `./scripts/run_remoterm.sh --host 0.0.0.0 --port 8000`\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

