"""Tests for community MQTT publisher."""

import json
from unittest.mock import MagicMock, patch

import nacl.bindings
import pytest

from app.community_mqtt import (
    _CLIENT_ID,
    _DEFAULT_BROKER,
    CommunityMqttPublisher,
    _base64url_encode,
    _calculate_packet_hash,
    _ed25519_sign_expanded,
    _format_raw_packet,
    _generate_jwt_token,
    community_mqtt_broadcast,
)
from app.models import AppSettings


def _make_test_keys() -> tuple[bytes, bytes]:
    """Generate a test MeshCore-format key pair.

    Returns (private_key_64_bytes, public_key_32_bytes).
    MeshCore format: scalar(32) || prefix(32), where scalar is already clamped.
    """
    import hashlib
    import os

    seed = os.urandom(32)
    expanded = hashlib.sha512(seed).digest()
    scalar = bytearray(expanded[:32])
    # Clamp scalar (standard Ed25519 clamping)
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    scalar = bytes(scalar)
    prefix = expanded[32:]

    private_key = scalar + prefix
    public_key = nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(scalar)
    return private_key, public_key


class TestBase64UrlEncode:
    def test_encodes_without_padding(self):
        result = _base64url_encode(b"\x00\x01\x02")
        assert "=" not in result

    def test_uses_url_safe_chars(self):
        # Bytes that would produce + and / in standard base64
        result = _base64url_encode(b"\xfb\xff\xfe")
        assert "+" not in result
        assert "/" not in result


class TestJwtGeneration:
    def test_token_has_three_parts(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        parts = token.split(".")
        assert len(parts) == 3

    def test_header_contains_ed25519_alg(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        header_b64 = token.split(".")[0]
        # Add padding for base64 decoding
        import base64

        padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded))
        assert header["alg"] == "Ed25519"
        assert header["typ"] == "JWT"

    def test_payload_contains_required_fields(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["publicKey"] == public_key.hex().upper()
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] - payload["iat"] == 86400
        assert payload["aud"] == _DEFAULT_BROKER
        assert payload["owner"] == public_key.hex().upper()
        assert payload["client"] == _CLIENT_ID
        assert "email" not in payload  # omitted when empty

    def test_payload_includes_email_when_provided(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key, email="test@example.com")
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["email"] == "test@example.com"

    def test_payload_uses_custom_audience(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key, audience="custom.broker.net")
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["aud"] == "custom.broker.net"

    def test_signature_is_valid_hex(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        sig_hex = token.split(".")[2]
        sig_bytes = bytes.fromhex(sig_hex)
        assert len(sig_bytes) == 64

    def test_signature_verifies(self):
        """Verify the JWT signature using nacl.bindings.crypto_sign_open."""
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        signature = bytes.fromhex(parts[2])

        # crypto_sign_open expects signature + message concatenated
        signed_message = signature + signing_input
        # This will raise if the signature is invalid
        verified = nacl.bindings.crypto_sign_open(signed_message, public_key)
        assert verified == signing_input


class TestEddsaSignExpanded:
    def test_produces_64_byte_signature(self):
        private_key, public_key = _make_test_keys()
        message = b"test message"
        sig = _ed25519_sign_expanded(message, private_key[:32], private_key[32:], public_key)
        assert len(sig) == 64

    def test_signature_verifies_with_nacl(self):
        private_key, public_key = _make_test_keys()
        message = b"hello world"
        sig = _ed25519_sign_expanded(message, private_key[:32], private_key[32:], public_key)

        signed_message = sig + message
        verified = nacl.bindings.crypto_sign_open(signed_message, public_key)
        assert verified == message

    def test_different_messages_produce_different_signatures(self):
        private_key, public_key = _make_test_keys()
        sig1 = _ed25519_sign_expanded(b"msg1", private_key[:32], private_key[32:], public_key)
        sig2 = _ed25519_sign_expanded(b"msg2", private_key[:32], private_key[32:], public_key)
        assert sig1 != sig2


class TestPacketFormatConversion:
    def test_basic_field_mapping(self):
        data = {
            "id": 1,
            "observation_id": 100,
            "timestamp": 1700000000,
            "data": "0a1b2c3d",
            "payload_type": "ADVERT",
            "snr": 5.5,
            "rssi": -90,
            "decrypted": False,
            "decrypted_info": None,
        }
        result = _format_raw_packet(data, "TestNode", "AABBCCDD" * 8)

        assert result["origin"] == "TestNode"
        assert result["origin_id"] == "AABBCCDD" * 8
        assert result["raw"] == "0A1B2C3D"
        assert result["SNR"] == "5.5"
        assert result["RSSI"] == "-90"
        assert result["type"] == "PACKET"
        assert result["direction"] == "rx"
        assert result["len"] == "4"

    def test_timestamp_is_iso8601(self):
        data = {"timestamp": 1700000000, "data": "00", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["timestamp"]
        assert "T" in result["timestamp"]

    def test_snr_rssi_unknown_when_none(self):
        data = {"timestamp": 0, "data": "00", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["SNR"] == "Unknown"
        assert result["RSSI"] == "Unknown"

    def test_packet_type_extraction(self):
        # Header 0x14 = type 5, route 0 (TRANSPORT_FLOOD): header + 4 transport + path_len.
        data = {"timestamp": 0, "data": "140102030400", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["packet_type"] == "5"
        assert result["route"] == "F"

    def test_route_mapping(self):
        # Test all 4 route types (matches meshcore-packet-capture)
        # TRANSPORT_FLOOD=0 -> "F", FLOOD=1 -> "F", DIRECT=2 -> "D", TRANSPORT_DIRECT=3 -> "T"
        samples = [
            ("000102030400", "F"),  # TRANSPORT_FLOOD: header + transport + path_len
            ("0100", "F"),  # FLOOD: header + path_len
            ("0200", "D"),  # DIRECT: header + path_len
            ("030102030400", "T"),  # TRANSPORT_DIRECT: header + transport + path_len
        ]
        for raw_hex, expected in samples:
            data = {"timestamp": 0, "data": raw_hex, "snr": None, "rssi": None}
            result = _format_raw_packet(data, "Node", "AA" * 32)
            assert result["route"] == expected

    def test_hash_is_16_uppercase_hex_chars(self):
        data = {"timestamp": 0, "data": "aabb", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert len(result["hash"]) == 16
        assert result["hash"] == result["hash"].upper()

    def test_empty_data_handled(self):
        data = {"timestamp": 0, "data": "", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["raw"] == ""
        assert result["len"] == "0"
        assert result["packet_type"] == "0"
        assert result["route"] == "U"

    def test_includes_reference_time_fields(self):
        data = {"timestamp": 0, "data": "0100aabb", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["time"]
        assert result["date"]
        assert result["payload_len"] == "2"

    def test_adds_path_for_direct_route(self):
        # route=2 (DIRECT), path_len=2, path=aa bb, payload=cc
        data = {"timestamp": 0, "data": "0202AABBCC", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["route"] == "D"
        assert result["path"] == "aa,bb"

    def test_direct_route_includes_empty_path_field(self):
        data = {"timestamp": 0, "data": "0200", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["route"] == "D"
        assert "path" in result
        assert result["path"] == ""

    def test_unknown_version_uses_defaults(self):
        # version=1 in high bits, type=5, route=1
        header = (1 << 6) | (5 << 2) | 1
        data = {"timestamp": 0, "data": f"{header:02x}00", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["packet_type"] == "0"
        assert result["route"] == "U"
        assert result["payload_len"] == "0"


class TestCalculatePacketHash:
    def test_empty_bytes_returns_zeroes(self):
        result = _calculate_packet_hash(b"")
        assert result == "0" * 16

    def test_returns_16_uppercase_hex_chars(self):
        # Simple flood packet: header(1) + path_len(1) + payload
        raw = bytes([0x01, 0x00, 0xAA, 0xBB])  # FLOOD, no path, payload=0xAABB
        result = _calculate_packet_hash(raw)
        assert len(result) == 16
        assert result == result.upper()

    def test_flood_packet_hash(self):
        """FLOOD route (0x01): no transport codes, header + path_len + payload."""
        import hashlib

        # Header 0x11 = route=FLOOD(1), payload_type=4(ADVERT): (4<<2)|1 = 0x11
        payload = b"\xde\xad"
        raw = bytes([0x11, 0x00]) + payload  # header, path_len=0, payload
        result = _calculate_packet_hash(raw)

        # Expected: sha256(payload_type_byte + payload_data)[:16].upper()
        expected = hashlib.sha256(bytes([4]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_transport_flood_skips_transport_codes(self):
        """TRANSPORT_FLOOD (0x00): has 4 bytes of transport codes after header."""
        import hashlib

        # Header 0x10 = route=TRANSPORT_FLOOD(0), payload_type=4: (4<<2)|0 = 0x10
        transport_codes = b"\x01\x02\x03\x04"
        payload = b"\xca\xfe"
        raw = bytes([0x10]) + transport_codes + bytes([0x00]) + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([4]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_transport_direct_skips_transport_codes(self):
        """TRANSPORT_DIRECT (0x03): also has 4 bytes of transport codes."""
        import hashlib

        # Header 0x13 = route=TRANSPORT_DIRECT(3), payload_type=4: (4<<2)|3 = 0x13
        transport_codes = b"\x05\x06\x07\x08"
        payload = b"\xbe\xef"
        raw = bytes([0x13]) + transport_codes + bytes([0x00]) + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([4]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_trace_packet_includes_path_len_in_hash(self):
        """TRACE packets (type 9) include path_len as uint16_t LE in the hash."""
        import hashlib

        # Header for TRACE with FLOOD route: (9<<2)|1 = 0x25
        path_len = 3
        path_data = b"\xaa\xbb\xcc"
        payload = b"\x01\x02"
        raw = bytes([0x25, path_len]) + path_data + payload
        result = _calculate_packet_hash(raw)

        expected_hash = (
            hashlib.sha256(bytes([9]) + path_len.to_bytes(2, byteorder="little") + payload)
            .hexdigest()[:16]
            .upper()
        )
        assert result == expected_hash

    def test_with_path_data(self):
        """Packet with non-zero path_len should skip path bytes to reach payload."""
        import hashlib

        # FLOOD route, payload_type=2 (TXT_MSG): (2<<2)|1 = 0x09
        path_data = b"\xaa\xbb"  # 2 bytes of path
        payload = b"\x48\x65\x6c\x6c\x6f"  # "Hello"
        raw = bytes([0x09, 0x02]) + path_data + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([2]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_truncated_packet_returns_zeroes(self):
        # Header says TRANSPORT_FLOOD, but missing path_len at required offset.
        raw = bytes([0x10, 0x01, 0x02])
        assert _calculate_packet_hash(raw) == "0" * 16


class TestCommunityMqttPublisher:
    def test_initial_state(self):
        pub = CommunityMqttPublisher()
        assert pub.connected is False
        assert pub._client is None
        assert pub._task is None

    @pytest.mark.asyncio
    async def test_publish_drops_when_disconnected(self):
        pub = CommunityMqttPublisher()
        # Should not raise
        await pub.publish("topic", {"key": "value"})

    @pytest.mark.asyncio
    async def test_stop_resets_state(self):
        pub = CommunityMqttPublisher()
        pub.connected = True
        pub._client = MagicMock()
        await pub.stop()
        assert pub.connected is False
        assert pub._client is None

    def test_is_configured_false_when_disabled(self):
        pub = CommunityMqttPublisher()
        pub._settings = AppSettings(community_mqtt_enabled=False)
        with patch("app.keystore.has_private_key", return_value=True):
            assert pub._is_configured() is False

    def test_is_configured_false_when_no_private_key(self):
        pub = CommunityMqttPublisher()
        pub._settings = AppSettings(community_mqtt_enabled=True)
        with patch("app.keystore.has_private_key", return_value=False):
            assert pub._is_configured() is False

    def test_is_configured_true_when_enabled_with_key(self):
        pub = CommunityMqttPublisher()
        pub._settings = AppSettings(community_mqtt_enabled=True)
        with patch("app.keystore.has_private_key", return_value=True):
            assert pub._is_configured() is True


class TestCommunityMqttBroadcast:
    def test_filters_non_raw_packet(self):
        """Non-raw_packet events should be ignored."""
        with patch("app.community_mqtt.community_publisher") as mock_pub:
            mock_pub.connected = True
            mock_pub._settings = AppSettings(community_mqtt_enabled=True)
            community_mqtt_broadcast("message", {"text": "hello"})
            # No asyncio.create_task should be called for non-raw_packet events
            # Since we're filtering, we just verify no exception

    def test_skips_when_disconnected(self):
        """Should not publish when disconnected."""
        with (
            patch("app.community_mqtt.community_publisher") as mock_pub,
            patch("app.community_mqtt.asyncio.create_task") as mock_task,
        ):
            mock_pub.connected = False
            mock_pub._settings = AppSettings(community_mqtt_enabled=True)
            community_mqtt_broadcast("raw_packet", {"data": "00"})
            mock_task.assert_not_called()

    def test_skips_when_settings_none(self):
        """Should not publish when settings are None."""
        with (
            patch("app.community_mqtt.community_publisher") as mock_pub,
            patch("app.community_mqtt.asyncio.create_task") as mock_task,
        ):
            mock_pub.connected = True
            mock_pub._settings = None
            community_mqtt_broadcast("raw_packet", {"data": "00"})
            mock_task.assert_not_called()


class TestPublishFailureSetsDisconnected:
    @pytest.mark.asyncio
    async def test_publish_error_sets_connected_false(self):
        """A publish error should set connected=False so the loop can detect it."""
        pub = CommunityMqttPublisher()
        pub.connected = True
        mock_client = MagicMock()
        mock_client.publish = MagicMock(side_effect=Exception("broker gone"))
        pub._client = mock_client
        await pub.publish("topic", {"data": "test"})
        assert pub.connected is False
