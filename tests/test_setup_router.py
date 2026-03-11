from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_setup_status_when_no_spi_config(monkeypatch, tmp_path):
    """If no SPI config is present, setup_required should be False."""
    # Point config_file to a non-existent path.
    monkeypatch.setenv("MESHCORE_CONFIG_FILE", str(tmp_path / "nope.yaml"))

    from importlib import reload

    from app import config

    reload(config)

    resp = client.get("/api/setup/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setup_required"] is False


def test_hardware_profiles_returns_known_profiles():
    resp = client.get("/api/setup/hardware-profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    ids = {p["id"] for p in profiles}
    # At least waveshare should be present.
    assert "waveshare" in ids


def test_radio_presets_uses_fallback(tmp_path, monkeypatch):
    """When API fetch fails, presets should come from fallback JSON if present."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fallback = data_dir / "radio-presets-fallback.json"
    fallback.write_text(
        json.dumps(
            [
                {
                    "title": "Test Region",
                    "frequency": 910.525,
                    "spreading_factor": 7,
                    "bandwidth": 62.5,
                    "coding_rate": 5,
                }
            ]
        )
    )
    monkeypatch.chdir(tmp_path)

    # Force API path to be skipped so fallback file is used (router uses module-level httpx).
    with patch("app.routers.setup.httpx", None):
        resp = client.get("/api/setup/radio-presets")

    assert resp.status_code == 200
    presets = resp.json()
    assert len(presets) == 1
    assert presets[0]["title"] == "Test Region"


def test_provision_writes_config(tmp_path, monkeypatch):
    """Provision endpoint should write a config.yaml with SPI settings."""
    # Use a temp data dir as CWD so spi_config_file.DEFAULT_CONFIG_PATH resolves there.
    monkeypatch.chdir(tmp_path)

    # Simple fake settings pointing to spi mode at DEFAULT_CONFIG_PATH.
    fake_settings = SimpleNamespace(
        spi_config_path=Path("data/config.yaml"),
        connection_type="spi",
    )

    with patch("app.routers.setup.settings", fake_settings):
        payload = {
            "node_name": "RemoteTerm-Test",
            "hardware_profile": "waveshare",
            "radio_preset": {
                "title": "Test Region",
                "frequency": 910.525,
                "spreading_factor": 7,
                "bandwidth": 62.5,
                "coding_rate": 5,
            },
            "latitude": 1.23,
            "longitude": 4.56,
        }
        resp = client.post("/api/setup/provision", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    cfg_path = Path(data["config_path"])
    assert cfg_path.is_file()

    import yaml  # type: ignore[import-untyped]

    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["node"]["name"] == "RemoteTerm-Test"
    assert cfg["node"]["latitude"] == 1.23
    assert cfg["node"]["longitude"] == 4.56
    assert cfg["hardware"]["profile"] == "waveshare"
    assert cfg["radio"]["frequency"] == 910525000
    assert cfg["radio"]["bandwidth"] == 62500
    assert cfg["radio"]["spreading_factor"] == 7
    assert cfg["radio"]["coding_rate"] == 5
