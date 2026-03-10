"""Hardware profiles for SPI-connected LoRa radio boards.

Each profile contains the GPIO pin mappings and SPI bus settings needed to
initialise an SX1262Radio instance for a specific board.  Values are sourced
from pymc_core examples and pyMC_Repeater's ``radio-settings.json``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HardwareProfile:
    """Pin and SPI configuration for a single board."""

    name: str
    bus_id: int = 0
    cs_id: int = 0
    cs_pin: int = -1
    reset_pin: int = 18
    busy_pin: int = 20
    irq_pin: int = 16
    txen_pin: int = -1
    rxen_pin: int = -1
    is_waveshare: bool = False
    use_dio3_tcxo: bool = False
    use_dio2_rf: bool = False
    default_tx_power: int = 22
    prerequisites: list[str] = field(default_factory=list)
    notes: str = ""


HARDWARE_PROFILES: dict[str, HardwareProfile] = {
    "waveshare": HardwareProfile(
        name="Waveshare LoRa HAT (SPI)",
        bus_id=0,
        cs_id=0,
        cs_pin=21,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=13,
        rxen_pin=12,
        is_waveshare=True,
        prerequisites=["Enable SPI via raspi-config"],
        notes="SPI version only. UART version is not supported.",
    ),
    "uconsole": HardwareProfile(
        name="uConsole LoRa Module (HackerGadgets All-In-One Board)",
        bus_id=1,
        cs_id=0,
        cs_pin=-1,
        reset_pin=25,
        busy_pin=24,
        irq_pin=26,
        txen_pin=-1,
        rxen_pin=-1,
        prerequisites=[
            "Enable SPI and SPI1 overlay: add 'dtparam=spi=on' and "
            "'dtoverlay=spi1-1cs' to /boot/firmware/config.txt",
            "Disable devterm-printer service if on Rex Bookworm: "
            "'sudo systemctl disable devterm-printer.service'",
            "Reboot after config changes",
        ],
        notes="Uses SPI1.  Has built-in GPS/RTC.",
    ),
    "pimesh-1w-usa": HardwareProfile(
        name="PiMesh-1W (USA)",
        bus_id=0,
        cs_id=0,
        cs_pin=21,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=13,
        rxen_pin=12,
        use_dio3_tcxo=True,
        default_tx_power=30,
        prerequisites=["Enable SPI via raspi-config"],
    ),
    "pimesh-1w-uk": HardwareProfile(
        name="PiMesh-1W (UK)",
        bus_id=0,
        cs_id=0,
        cs_pin=21,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=13,
        rxen_pin=12,
        use_dio3_tcxo=True,
        prerequisites=["Enable SPI via raspi-config"],
    ),
    "meshadv-mini": HardwareProfile(
        name="FrequencyLabs meshadv-mini",
        bus_id=0,
        cs_id=0,
        cs_pin=8,
        reset_pin=24,
        busy_pin=20,
        irq_pin=16,
        txen_pin=-1,
        rxen_pin=12,
        prerequisites=["Enable SPI via raspi-config"],
    ),
    "meshadv": HardwareProfile(
        name="FrequencyLabs meshadv",
        bus_id=0,
        cs_id=0,
        cs_pin=21,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=13,
        rxen_pin=12,
        use_dio3_tcxo=True,
        prerequisites=["Enable SPI via raspi-config"],
    ),
    "ht-ra62": HardwareProfile(
        name="Heltec HT-RA62 Module",
        bus_id=0,
        cs_id=0,
        cs_pin=21,
        reset_pin=18,
        busy_pin=20,
        irq_pin=16,
        txen_pin=-1,
        rxen_pin=-1,
        use_dio3_tcxo=True,
        use_dio2_rf=True,
        prerequisites=["Enable SPI via raspi-config"],
    ),
}


def get_profile(name: str) -> HardwareProfile:
    """Return the profile for *name*, raising ``ValueError`` if unknown."""
    try:
        return HARDWARE_PROFILES[name]
    except KeyError:
        valid = ", ".join(sorted(HARDWARE_PROFILES))
        raise ValueError(f"Unknown hardware profile {name!r}. Valid profiles: {valid}") from None
