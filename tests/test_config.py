"""Tests for configuration validation.

These tests verify transport mutual exclusivity, BLE PIN requirement,
and connection_type derivation.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings


class TestTransportExclusivity:
    """Ensure only one transport can be configured at a time."""

    def test_no_transport_defaults_to_serial(self):
        """No transport env vars means serial auto-detect."""
        s = Settings(serial_port="", tcp_host="", ble_address="")
        assert s.connection_type == "serial"

    def test_serial_only(self):
        s = Settings(serial_port="/dev/ttyUSB0")
        assert s.connection_type == "serial"

    def test_tcp_only(self):
        s = Settings(tcp_host="192.168.1.1")
        assert s.connection_type == "tcp"

    def test_tcp_with_custom_port(self):
        s = Settings(tcp_host="192.168.1.1", tcp_port=5000)
        assert s.connection_type == "tcp"
        assert s.tcp_port == 5000

    def test_tcp_default_port(self):
        s = Settings(tcp_host="192.168.1.1")
        assert s.tcp_port == 5000

    def test_ble_only(self):
        s = Settings(ble_address="AA:BB:CC:DD:EE:FF", ble_pin="123456")
        assert s.connection_type == "ble"

    def test_serial_and_tcp_rejected(self):
        with pytest.raises(ValidationError, match="Only one transport"):
            Settings(serial_port="/dev/ttyUSB0", tcp_host="192.168.1.1")

    def test_serial_and_ble_rejected(self):
        with pytest.raises(ValidationError, match="Only one transport"):
            Settings(
                serial_port="/dev/ttyUSB0",
                ble_address="AA:BB:CC:DD:EE:FF",
                ble_pin="123456",
            )

    def test_tcp_and_ble_rejected(self):
        with pytest.raises(ValidationError, match="Only one transport"):
            Settings(
                tcp_host="192.168.1.1",
                ble_address="AA:BB:CC:DD:EE:FF",
                ble_pin="123456",
            )

    def test_all_three_rejected(self):
        with pytest.raises(ValidationError, match="Only one transport"):
            Settings(
                serial_port="/dev/ttyUSB0",
                tcp_host="192.168.1.1",
                ble_address="AA:BB:CC:DD:EE:FF",
                ble_pin="123456",
            )


class TestBLEPinRequirement:
    """BLE address requires a PIN."""

    def test_ble_address_without_pin_rejected(self):
        with pytest.raises(ValidationError, match="MESHCORE_BLE_PIN is required"):
            Settings(ble_address="AA:BB:CC:DD:EE:FF", ble_pin="")

    def test_ble_address_with_pin_accepted(self):
        s = Settings(ble_address="AA:BB:CC:DD:EE:FF", ble_pin="123456")
        assert s.ble_address == "AA:BB:CC:DD:EE:FF"
        assert s.ble_pin == "123456"


class TestBasicAuthConfiguration:
    """Ensure basic auth credentials are configured as a pair."""

    def test_basic_auth_disabled_by_default(self):
        s = Settings(serial_port="", tcp_host="", ble_address="")
        assert s.basic_auth_enabled is False

    def test_basic_auth_enabled_when_both_credentials_are_set(self):
        s = Settings(
            serial_port="",
            tcp_host="",
            ble_address="",
            basic_auth_username="mesh",
            basic_auth_password="secret",
        )
        assert s.basic_auth_enabled is True

    def test_basic_auth_requires_password_with_username(self):
        with pytest.raises(ValidationError, match="MESHCORE_BASIC_AUTH_USERNAME"):
            Settings(
                serial_port="",
                tcp_host="",
                ble_address="",
                basic_auth_username="mesh",
                basic_auth_password="",
            )

    def test_basic_auth_requires_username_with_password(self):
        with pytest.raises(ValidationError, match="MESHCORE_BASIC_AUTH_USERNAME"):
            Settings(
                serial_port="",
                tcp_host="",
                ble_address="",
                basic_auth_username="",
                basic_auth_password="secret",
            )


class TestExperimentalAliases:
    """Ensure exact-name experimental env vars still map into settings."""

    def test_clowntown_wraparound_alias_reads_exact_env_var(self, monkeypatch):
        monkeypatch.setenv("__CLOWNTOWN_DO_CLOCK_WRAPAROUND", "true")
        s = Settings(serial_port="", tcp_host="", ble_address="")
        assert s.clowntown_do_clock_wraparound is True
