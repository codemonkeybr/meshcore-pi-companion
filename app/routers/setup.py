from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

try:
    import httpx
except ImportError:  # optional dependency; tests may patch this to force fallback
    httpx = None  # type: ignore[assignment]

from app.backends.spi_config import HARDWARE_PROFILES
from app.config import settings
from app.spi_config_file import DEFAULT_CONFIG_PATH, load_config, save_config

router = APIRouter(tags=["setup"])


def _spi_config_present() -> bool:
    """Return True if an SPI config file exists at the resolved path."""
    return settings.spi_config_path is not None


def _spi_config_valid(path: Path) -> bool:
    """Return True if the SPI config file can be loaded successfully."""
    try:
        load_config(path)
        return True
    except Exception:
        return False


def get_setup_required() -> bool:
    """Return True when SPI mode is selected but config is missing or invalid (for health/setup UI)."""
    if settings.connection_type != "spi":
        return False
    cfg_path = settings.spi_config_path
    if cfg_path is None:
        return False
    return not _spi_config_valid(cfg_path)


@router.get("/setup/status")
async def get_setup_status() -> dict[str, Any]:
    """Report whether SPI provisioning is required."""
    cfg_path = settings.spi_config_path
    if cfg_path is None:
        # No SPI config file; if serial/TCP/BLE are configured instead, this is fine.
        return {"setup_required": False, "mode": settings.connection_type}

    valid = _spi_config_valid(cfg_path)
    return {
        "setup_required": not valid,
        "mode": "spi",
        "config_path": str(cfg_path),
    }


@router.get("/setup/hardware-profiles")
async def get_hardware_profiles() -> list[dict[str, Any]]:
    """Return the list of supported SPI hardware profiles."""
    profiles: list[dict[str, Any]] = []
    for key, profile in sorted(HARDWARE_PROFILES.items()):
        profiles.append(
            {
                "id": key,
                "name": profile.name,
                "bus_id": profile.bus_id,
                "cs_pin": profile.cs_pin,
                "reset_pin": profile.reset_pin,
                "busy_pin": profile.busy_pin,
                "irq_pin": profile.irq_pin,
                "txen_pin": profile.txen_pin,
                "rxen_pin": profile.rxen_pin,
                "prerequisites": profile.prerequisites,
                "notes": profile.notes,
            }
        )
    return profiles


def _fetch_radio_presets() -> list[dict[str, Any]]:
    """Fetch radio presets from the MeshCore API, with local JSON fallback."""
    presets: list[dict[str, Any]] = []

    if httpx is not None:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get("https://api.meshcore.nz/api/v1/config")
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

    if not presets:
        fallback_path = Path("data/radio-presets-fallback.json")
        if fallback_path.is_file():
            try:
                with fallback_path.open() as f:
                    data = json.load(f)
                if isinstance(data, list):
                    presets = data
            except Exception:
                pass

    return presets


@router.get("/setup/radio-presets")
async def get_radio_presets() -> list[dict[str, Any]]:
    """Expose radio presets for SPI provisioning UIs."""
    presets = _fetch_radio_presets()
    return presets


@router.post("/setup/provision")
async def provision_spi_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Write or update the SPI config.yaml based on provision payload.

    Expected payload keys (all optional, but some must be provided):
      - node_name: str
      - hardware_profile: str (key from HARDWARE_PROFILES)
      - radio_preset: object from /setup/radio-presets (frequency MHz, etc.)
      - frequency_hz: int
      - spreading_factor: int
      - bandwidth_hz: int
      - coding_rate: int
      - latitude: float
      - longitude: float
    """
    cfg_path = settings.spi_config_path or DEFAULT_CONFIG_PATH

    # Start with an existing config if present; otherwise empty.
    if cfg_path.is_file():
        base = load_config(cfg_path)
    else:
        base = {
            "node": {},
            "radio": {},
            "hardware": {},
            "logging": {"level": "INFO"},
        }

    node = dict(base.get("node") or {})
    radio = dict(base.get("radio") or {})
    hardware = dict(base.get("hardware") or {})

    node_name = payload.get("node_name")
    if node_name:
        node["name"] = str(node_name)

    lat = payload.get("latitude")
    lon = payload.get("longitude")
    if lat is not None:
        node["latitude"] = float(lat)
    if lon is not None:
        node["longitude"] = float(lon)

    profile_key = payload.get("hardware_profile")
    if profile_key:
        if profile_key not in HARDWARE_PROFILES:
            raise HTTPException(status_code=400, detail="Unknown hardware_profile")
        hardware["profile"] = profile_key

    preset = payload.get("radio_preset") or {}
    if preset:
        try:
            freq_mhz = float(preset.get("frequency", 0.0))
            bw_khz = float(preset.get("bandwidth", 62.5))
            sf = int(preset.get("spreading_factor", 7))
            cr = int(preset.get("coding_rate", 5))
            radio["frequency"] = int(freq_mhz * 1_000_000)
            radio["bandwidth"] = int(bw_khz * 1_000)
            radio["spreading_factor"] = sf
            radio["coding_rate"] = cr
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail=f"Invalid radio_preset: {exc}") from exc

    # Explicit overrides
    if "frequency_hz" in payload:
        radio["frequency"] = int(payload["frequency_hz"])
    if "bandwidth_hz" in payload:
        radio["bandwidth"] = int(payload["bandwidth_hz"])
    if "spreading_factor" in payload:
        radio["spreading_factor"] = int(payload["spreading_factor"])
    if "coding_rate" in payload:
        radio["coding_rate"] = int(payload["coding_rate"])

    # Ensure defaults for fields that load_config would apply.
    radio.setdefault("tx_power", 22)
    radio.setdefault("preamble_length", 17)
    radio.setdefault("sync_word", 13380)

    base["node"] = node
    base["radio"] = radio
    base["hardware"] = hardware

    save_config(base, cfg_path)

    return {
        "status": "ok",
        "config_path": str(cfg_path),
    }
