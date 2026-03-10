import logging
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MESHCORE_")

    serial_port: str = ""  # Empty string triggers auto-detection
    serial_baudrate: int = 115200
    tcp_host: str = ""
    tcp_port: int = 4000
    ble_address: str = ""
    ble_pin: str = ""
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_path: str = "data/meshcore.db"
    disable_bots: bool = False

    # SPI backend: path to config.yaml (auto-detected at data/config.yaml)
    config_file: str = "data/config.yaml"

    @model_validator(mode="after")
    def validate_transport_exclusivity(self) -> "Settings":
        from pathlib import Path

        spi_active = Path(self.config_file).is_file()
        transports_set = sum(
            [
                bool(self.serial_port),
                bool(self.tcp_host),
                bool(self.ble_address),
                spi_active,
            ]
        )
        if transports_set > 1:
            raise ValueError(
                "Only one transport may be configured at a time. "
                "Set exactly one of MESHCORE_SERIAL_PORT, MESHCORE_TCP_HOST, "
                "MESHCORE_BLE_ADDRESS, or provide a config file at "
                f"{self.config_file} for SPI mode."
            )
        if self.ble_address and not self.ble_pin:
            raise ValueError("MESHCORE_BLE_PIN is required when MESHCORE_BLE_ADDRESS is set.")
        return self

    @property
    def connection_type(self) -> Literal["serial", "tcp", "ble", "spi"]:
        from pathlib import Path

        if Path(self.config_file).is_file():
            return "spi"
        if self.tcp_host:
            return "tcp"
        if self.ble_address:
            return "ble"
        return "serial"


settings = Settings()


class _RepeatSquelch(logging.Filter):
    """Suppress rapid-fire identical messages and emit a summary instead.

    Attached to the ``meshcore`` library logger to catch its repeated
    "Serial Connection started" lines that flood the log when another
    process holds the serial port.
    """

    def __init__(self, threshold: int = 3) -> None:
        super().__init__()
        self._last_msg: str | None = None
        self._repeat_count: int = 0
        self._threshold = threshold

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if msg == self._last_msg:
            self._repeat_count += 1
            if self._repeat_count == self._threshold:
                record.msg = (
                    "%s (repeated %d times — possible serial port contention from another process)"
                )
                record.args = (msg, self._repeat_count)
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
                return True
            # Suppress further repeats beyond the threshold
            return self._repeat_count < self._threshold
        else:
            self._last_msg = msg
            self._repeat_count = 1
            return True


def setup_logging() -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Squelch repeated messages from the meshcore library (e.g. rapid-fire
    # "Serial Connection started" when the port is contended).
    logging.getLogger("meshcore").addFilter(_RepeatSquelch())
