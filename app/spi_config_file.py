"""Load and validate the SPI node config file (``data/config.yaml``).

The config file follows the same style as pyMC_Repeater's ``config.yaml``:
sections for ``node``, ``radio``, ``hardware``, and ``logging``.

When the file exists, RemoteTerm starts in SPI mode — no environment
variables needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("data/config.yaml")

# Defaults matching the USA/Canada (Recommended) community preset
_RADIO_DEFAULTS: dict[str, Any] = {
    "frequency": 910525000,
    "tx_power": 22,
    "bandwidth": 62500,
    "spreading_factor": 7,
    "coding_rate": 5,
    "preamble_length": 17,
    "sync_word": 13380,
}


def config_file_exists(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """Return ``True`` when an SPI config file is present."""
    return path.is_file()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the SPI config file.

    Returns the full config dict with defaults applied for any missing values.
    Raises ``FileNotFoundError`` if the file does not exist.
    Raises ``RuntimeError`` on parse errors or missing required fields.
    """
    import yaml  # lazy — only available when [spi] extra is installed

    if not path.is_file():
        raise FileNotFoundError(
            f"SPI config file not found: {path}\n"
            f"Copy config.yaml.example to {path} and adjust for your setup."
        )

    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to parse {path}: {exc}") from exc

    # ---- node section ----
    node = raw.get("node", {})
    node.setdefault("name", "")
    node.setdefault("latitude", 0.0)
    node.setdefault("longitude", 0.0)

    # ---- radio section ----
    radio = raw.get("radio", {})
    for key, default in _RADIO_DEFAULTS.items():
        radio.setdefault(key, default)

    # ---- hardware section (required) ----
    hardware = raw.get("hardware", {})
    if not hardware.get("profile"):
        raise RuntimeError(
            f"Config file {path} is missing hardware.profile. "
            f"Set it to one of: waveshare, uconsole, pimesh-1w-usa, etc."
        )

    # ---- logging section ----
    log_section = raw.get("logging", {})
    log_section.setdefault("level", "INFO")

    config: dict[str, Any] = {
        "node": node,
        "radio": radio,
        "hardware": hardware,
        "logging": log_section,
    }

    logger.info("Loaded SPI config from %s (profile=%s)", path, hardware["profile"])
    return config


def save_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write the SPI config dict to a YAML file.

    Creates the parent directory if needed. Does not validate the config;
    use load_config() after to verify.
    """
    import yaml  # lazy — only available when [spi] extra is installed

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    logger.info("Saved SPI config to %s", path)
