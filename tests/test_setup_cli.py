"""Tests for the SPI CLI setup wizard (app.setup_cli)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _make_minimal_config_example(tmp_path: Path) -> None:
    cfg_example = tmp_path / "config.yaml.example"
    cfg_example.write_text(
        "node:\n"
        "  name: RemoteTerm\n"
        "  latitude: 0.0\n"
        "  longitude: 0.0\n"
        "radio:\n"
        "  frequency: 910525000\n"
        "  tx_power: 22\n"
        "  bandwidth: 62500\n"
        "  spreading_factor: 7\n"
        "  coding_rate: 5\n"
        "hardware:\n"
        "  profile: waveshare\n"
        "logging:\n"
        "  level: INFO\n"
    )


def test_setup_cli_creates_config_from_example(tmp_path, monkeypatch):
    """Running the wizard with defaults should create a valid config.yaml."""
    monkeypatch.chdir(tmp_path)
    _make_minimal_config_example(tmp_path)

    # Provide a single hardware profile so the menu is deterministic.
    fake_profile = SimpleNamespace(name="Waveshare LoRa HAT (SPI)")
    with patch(
        "app.setup_cli.HARDWARE_PROFILES",
        {"waveshare": fake_profile},
    ):
        # Use a single fake preset and patch the fetch helper.
        with patch(
            "app.setup_cli._fetch_radio_presets",
            return_value=[
                {
                    "title": "USA/Canada (Recommended)",
                    "frequency": 910.525,
                    "spreading_factor": 7,
                    "bandwidth": 62.5,
                    "coding_rate": 5,
                }
            ],
        ):
            # Simulate user pressing Enter at every prompt (accept defaults).
            inputs = iter(["", "1", "1", "", ""])

            def fake_input(prompt: str) -> str:  # noqa: D401
                return next(inputs)

            monkeypatch.setattr("builtins.input", fake_input)

            from app import setup_cli

            rc = setup_cli.main()
            assert rc == 0

    cfg_path = tmp_path / "config.yaml"
    assert cfg_path.is_file()

    import yaml  # type: ignore[import-untyped]

    data = yaml.safe_load(cfg_path.read_text())
    assert data["node"]["name"].startswith("RemoteTerm")
    assert data["hardware"]["profile"] == "waveshare"
    assert data["radio"]["frequency"] == 910525000
    assert data["radio"]["bandwidth"] == 62500
    assert data["radio"]["spreading_factor"] == 7
    assert data["radio"]["coding_rate"] == 5


def test_setup_cli_uses_existing_config_when_present(tmp_path, monkeypatch):
    """If config.yaml already exists, it should be updated in-place."""
    monkeypatch.chdir(tmp_path)

    # Existing config.yaml with a different name and radio settings.
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "node:\n"
        "  name: ExistingNode\n"
        "radio:\n"
        "  frequency: 869618000\n"
        "  tx_power: 10\n"
        "  bandwidth: 50000\n"
        "  spreading_factor: 8\n"
        "  coding_rate: 8\n"
        "hardware:\n"
        "  profile: waveshare\n"
    )

    fake_profile = SimpleNamespace(name="Waveshare LoRa HAT (SPI)")
    with patch(
        "app.setup_cli.HARDWARE_PROFILES",
        {"waveshare": fake_profile},
    ):
        with patch(
            "app.setup_cli._fetch_radio_presets",
            return_value=[
                {
                    "title": "EU/UK (Narrow)",
                    "frequency": 869.618,
                    "spreading_factor": 8,
                    "bandwidth": 62.5,
                    "coding_rate": 8,
                }
            ],
        ):
            # Accept existing node name, choose first (and only) hardware/preset,
            # and leave lat/lon defaults.
            inputs = iter(["", "1", "1", "", ""])

            def fake_input(prompt: str) -> str:
                return next(inputs)

            monkeypatch.setattr("builtins.input", fake_input)

            from app import setup_cli

            rc = setup_cli.main()
            assert rc == 0

    import yaml  # type: ignore[import-untyped]

    data = yaml.safe_load(cfg.read_text())
    # Node name should still be non-empty.
    assert data["node"]["name"]
    # Hardware profile remains waveshare.
    assert data["hardware"]["profile"] == "waveshare"
    # Radio settings updated from preset (869.618 MHz and 62.5 kHz).
    assert data["radio"]["frequency"] == 869618000
    assert data["radio"]["bandwidth"] == 62500
