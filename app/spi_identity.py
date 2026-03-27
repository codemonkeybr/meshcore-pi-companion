"""Ed25519 identity management for SPI-mode nodes.

Handles generation, persistence, and loading of the 32-byte seed that
serves as the node's mesh identity.  The seed is stored base64-encoded
as ``identity_key`` inside ``data/config.yaml`` (alongside radio/hardware settings).
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("data/config.yaml")


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def generate_identity_seed() -> bytes:
    """Return 32 cryptographically-random bytes suitable for Ed25519 keying."""
    return os.urandom(32)


def load_spi_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load the config file, returning an empty dict if it doesn't exist."""
    import yaml  # lazy — only available when [spi] extra is installed

    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_spi_config(config: dict[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persist *config* to the config file, merging with any existing content."""
    import yaml  # lazy — only available when [spi] extra is installed

    _ensure_dir(path)
    # Load existing file to preserve all other keys (node, radio, hardware, etc.)
    existing: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            existing = yaml.safe_load(f) or {}
    existing.update(config)
    with open(path, "w") as f:
        yaml.safe_dump(existing, f, sort_keys=False)
    logger.info("SPI config saved to %s", path)


def load_or_create_identity(path: Path = DEFAULT_CONFIG_PATH) -> bytes:
    """Load the identity seed from disk, generating a new one if absent.

    Returns the 32-byte seed.
    """
    config = load_spi_config(path)
    encoded = config.get("identity_key")
    if encoded:
        seed = base64.b64decode(encoded)
        logger.info("Loaded existing SPI identity from %s", path)
        return seed

    seed = generate_identity_seed()
    config["identity_key"] = base64.b64encode(seed).decode()
    save_spi_config(config, path)
    logger.info("Generated new SPI identity and saved to %s", path)
    return seed


def import_identity(seed: bytes, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Replace the stored identity with *seed* (32 bytes)."""
    if len(seed) != 32:
        raise ValueError(f"Identity seed must be 32 bytes, got {len(seed)}")
    config = load_spi_config(path)
    config["identity_key"] = base64.b64encode(seed).decode()
    save_spi_config(config, path)
    logger.info("Imported new SPI identity to %s", path)


def export_identity(path: Path = DEFAULT_CONFIG_PATH) -> bytes | None:
    """Return the raw 32-byte seed, or ``None`` if no identity is stored."""
    config = load_spi_config(path)
    encoded = config.get("identity_key")
    if not encoded:
        return None
    return base64.b64decode(encoded)
