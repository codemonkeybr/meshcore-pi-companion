"""Tests for community MQTT publisher."""

import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nacl.bindings
import pytest

from app.fanout.community_mqtt import (
    _DEFAULT_BROKER,
    _STATS_REFRESH_INTERVAL,
    CommunityMqttPublisher,
    _base64url_encode,
    _build_radio_info,
    _build_status_topic,
    _calculate_packet_hash,
    _decode_packet_fields,
    _ed25519_sign_expanded,
    _format_raw_packet,
    _generate_jwt_token,
    _get_client_version,
)
from app.fanout.mqtt_community import (
    _config_to_settings,
    _publish_community_packet,
    _render_packet_topic,
)


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


def _make_community_settings(**overrides) -> SimpleNamespace:
    """Create a settings namespace with all community MQTT fields."""
    defaults = {
        "community_mqtt_enabled": True,
        "community_mqtt_broker_host": "mqtt-us-v1.letsmesh.net",
        "community_mqtt_broker_port": 443,
        "community_mqtt_transport": "websockets",
        "community_mqtt_use_tls": True,
        "community_mqtt_tls_verify": True,
        "community_mqtt_auth_mode": "token",
        "community_mqtt_username": "",
        "community_mqtt_password": "",
        "community_mqtt_iata": "",
        "community_mqtt_email": "",
        "community_mqtt_owner": "",
        "community_mqtt_token_audience": "mqtt-us-v1.letsmesh.net",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
        token, _ = _generate_jwt_token(private_key, public_key)
        parts = token.split(".")
        assert len(parts) == 3

    def test_header_contains_ed25519_alg(self):
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key)
        header_b64 = token.split(".")[0]
        # Add padding for base64 decoding
        import base64

        padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded))
        assert header["alg"] == "Ed25519"
        assert header["typ"] == "JWT"

    def test_payload_contains_required_fields(self):
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key)
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["publicKey"] == public_key.hex().upper()
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] - payload["iat"] == 86400
        assert payload["aud"] == _DEFAULT_BROKER
        # Main branch / broker format: owner = pubkey hex, client = identifier
        assert payload["owner"] == public_key.hex().upper()
        assert "client" in payload
        # email only present when provided
        assert payload.get("email", "") == ""

    def test_payload_includes_email_when_provided(self):
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key, email="test@example.com")
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["email"] == "test@example.com"

    def test_payload_uses_custom_audience(self):
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key, audience="custom.broker.net")
        payload_b64 = token.split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["aud"] == "custom.broker.net"

    def test_signature_is_valid_hex(self):
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key)
        sig_hex = token.split(".")[2]
        sig_bytes = bytes.fromhex(sig_hex)
        assert len(sig_bytes) == 64

    def test_signature_verifies(self):
        """Verify the JWT signature using nacl.bindings.crypto_sign_open."""
        private_key, public_key = _make_test_keys()
        token, _ = _generate_jwt_token(private_key, public_key)
        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        signature = bytes.fromhex(parts[2])

        # crypto_sign_open expects signature + message concatenated
        signed_message = signature + signing_input
        # This will raise if the signature is invalid
        verified = nacl.bindings.crypto_sign_open(signed_message, public_key)
        assert verified == signing_input

    def test_spi_seed_jwt_returns_standard_pubkey_and_verifies(self):
        """With 32-byte SPI seed, JWT uses standard Ed25519 and returns (token, pubkey_hex)."""
        from nacl.signing import SigningKey

        seed = b"\x00" * 31 + b"\x01"  # deterministic test seed
        standard_pubkey = SigningKey(seed).verify_key.encode()
        token, jwt_pubkey_hex = _generate_jwt_token(seed, standard_pubkey)
        assert jwt_pubkey_hex == standard_pubkey.hex().upper()
        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        signature = bytes.fromhex(parts[2])
        signed_message = signature + signing_input
        verified = nacl.bindings.crypto_sign_open(signed_message, standard_pubkey)
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
        data = {"timestamp": 0, "data": "140102030400AA", "snr": None, "rssi": None}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["packet_type"] == "5"
        assert result["route"] == "F"

    def test_route_mapping(self):
        # Test all 4 route types (matches meshcore-packet-capture)
        # TRANSPORT_FLOOD=0 -> "F", FLOOD=1 -> "F", DIRECT=2 -> "D", TRANSPORT_DIRECT=3 -> "T"
        samples = [
            ("000102030400AA", "F"),  # TRANSPORT_FLOOD: header + transport + path_len + payload
            ("0100AA", "F"),  # FLOOD: header + path_len + payload
            ("0200AA", "D"),  # DIRECT: header + path_len + payload
            ("030102030400AA", "T"),  # TRANSPORT_DIRECT: header + transport + path_len + payload
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
        data = {"timestamp": 0, "data": "0200AA", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["route"] == "D"
        assert "path" in result
        assert result["path"] == ""

    def test_direct_multibyte_route_formats_path_as_hop_identifiers(self):
        # route=2 (DIRECT), payload_type=2, path_byte=0x82 -> 2 hops x 3 bytes
        data = {"timestamp": 0, "data": "0A82AABBCCDDEEFF11", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["route"] == "D"
        assert result["payload_len"] == "1"
        assert result["path"] == "aabbcc,ddeeff"

    def test_flood_multibyte_route_omits_path_field(self):
        # route=1 (FLOOD), payload_type=2, path_byte=0x82 -> 2 hops x 3 bytes
        data = {"timestamp": 0, "data": "0982AABBCCDDEEFF11", "snr": 1.0, "rssi": -70}
        result = _format_raw_packet(data, "Node", "AA" * 32)
        assert result["route"] == "F"
        assert "path" not in result

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

    def test_multibyte_2byte_hops_skips_correct_path_length(self):
        """Mode 1 (2-byte hops), 2 hops → 4 bytes of path data to skip."""
        import hashlib

        # FLOOD route, payload_type=2 (TXT_MSG): (2<<2)|1 = 0x09
        # path_byte = 0x42 → mode=1, hop_count=2 → path_wire_len = 2*2 = 4
        path_data = b"\xaa\xbb\xcc\xdd"
        payload = b"\x48\x65\x6c\x6c\x6f"  # "Hello"
        raw = bytes([0x09, 0x42]) + path_data + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([2]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_multibyte_3byte_hops_skips_correct_path_length(self):
        """Mode 2 (3-byte hops), 1 hop → 3 bytes of path data to skip."""
        import hashlib

        # FLOOD route, payload_type=4 (ADVERT): (4<<2)|1 = 0x11
        # path_byte = 0x81 → mode=2, hop_count=1 → path_wire_len = 1*3 = 3
        path_data = b"\xaa\xbb\xcc"
        payload = b"\xde\xad"
        raw = bytes([0x11, 0x81]) + path_data + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([4]) + payload).hexdigest()[:16].upper()
        assert result == expected

    def test_trace_multibyte_uses_raw_wire_byte_in_hash(self):
        """TRACE with multi-byte hops uses raw path_byte (not decoded hop count) in hash."""
        import hashlib

        # TRACE with FLOOD route: (9<<2)|1 = 0x25
        # path_byte = 0x42 → mode=1, hop_count=2 → path_wire_len = 4
        path_byte = 0x42
        path_data = b"\xaa\xbb\xcc\xdd"
        payload = b"\x01\x02"
        raw = bytes([0x25, path_byte]) + path_data + payload
        result = _calculate_packet_hash(raw)

        # TRACE hash includes raw path_byte as uint16_t LE (0x42, 0x00)
        expected = (
            hashlib.sha256(bytes([9]) + path_byte.to_bytes(2, byteorder="little") + payload)
            .hexdigest()[:16]
            .upper()
        )
        assert result == expected

    def test_multibyte_truncated_path_returns_zeroes(self):
        """Packet claiming multi-byte path but truncated before path ends → zero hash."""
        # FLOOD route, payload_type=2: (2<<2)|1 = 0x09
        # path_byte = 0x42 → mode=1, hop_count=2 → needs 4 bytes of path, only 2 present
        raw = bytes([0x09, 0x42, 0xAA, 0xBB])
        assert _calculate_packet_hash(raw) == "0" * 16

    def test_reserved_mode_returns_zeroes(self):
        raw = bytes([0x09, 0xC1, 0xAA, 0xBB, 0xCC])
        assert _calculate_packet_hash(raw) == "0" * 16

    def test_oversize_path_len_returns_zeroes(self):
        raw = bytes([0x09, 0xBF]) + bytes(189) + b"payload"
        assert _calculate_packet_hash(raw) == "0" * 16

    def test_no_payload_returns_zeroes(self):
        raw = bytes([0x09, 0x02, 0xAA, 0xBB])
        assert _calculate_packet_hash(raw) == "0" * 16

    def test_multibyte_transport_flood_with_2byte_hops(self):
        """TRANSPORT_FLOOD with 2-byte hops correctly skips transport codes + path."""
        import hashlib

        # TRANSPORT_FLOOD (0x00), payload_type=4: (4<<2)|0 = 0x10
        transport_codes = b"\x01\x02\x03\x04"
        # path_byte = 0x41 → mode=1, hop_count=1 → path_wire_len = 2
        path_data = b"\xaa\xbb"
        payload = b"\xca\xfe"
        raw = bytes([0x10]) + transport_codes + bytes([0x41]) + path_data + payload
        result = _calculate_packet_hash(raw)

        expected = hashlib.sha256(bytes([4]) + payload).hexdigest()[:16].upper()
        assert result == expected


class TestDecodePacketFieldsMultibyte:
    """Test _decode_packet_fields with multi-byte hop paths."""

    def test_1byte_hops_legacy(self):
        """Legacy packet with mode=0, 3 hops → 3 path bytes."""
        # FLOOD route, payload_type=2 (TXT_MSG): (2<<2)|1 = 0x09
        path_data = b"\xaa\xbb\xcc"
        payload = b"\x48\x65\x6c\x6c\x6f"
        raw = bytes([0x09, 0x03]) + path_data + payload

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert route == "F"
        assert ptype == "2"
        assert path_values == ["aa", "bb", "cc"]
        assert payload_type == 2

    def test_2byte_hops(self):
        """Mode 1, 2 hops → 4 path bytes split into 2 two-byte identifiers."""
        # FLOOD route, payload_type=2: (2<<2)|1 = 0x09
        # path_byte = 0x42 → mode=1, hop_count=2
        path_data = b"\xaa\xbb\xcc\xdd"
        payload = b"\x48\x65\x6c\x6c\x6f"
        raw = bytes([0x09, 0x42]) + path_data + payload

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert route == "F"
        assert ptype == "2"
        assert path_values == ["aabb", "ccdd"]
        assert payload_type == 2
        assert plen == str(len(payload))

    def test_3byte_hops(self):
        """Mode 2, 1 hop → 3 path bytes as a single three-byte identifier."""
        # FLOOD route, payload_type=4 (ADVERT): (4<<2)|1 = 0x11
        # path_byte = 0x81 → mode=2, hop_count=1
        path_data = b"\xaa\xbb\xcc"
        payload = b"\xde\xad"
        raw = bytes([0x11, 0x81]) + path_data + payload

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert route == "F"
        assert ptype == "4"
        assert path_values == ["aabbcc"]
        assert payload_type == 4

    def test_direct_packet_no_path(self):
        """Direct packet (0 hops) → empty path list."""
        # FLOOD route, payload_type=2: (2<<2)|1 = 0x09
        # path_byte = 0x00 → mode=0, hop_count=0
        payload = b"\x48\x65\x6c\x6c\x6f"
        raw = bytes([0x09, 0x00]) + payload

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert path_values == []
        assert plen == str(len(payload))

    def test_transport_flood_2byte_hops(self):
        """TRANSPORT_FLOOD with 2-byte hops: skip 4 transport bytes, then decode path."""
        # TRANSPORT_FLOOD (0x00), payload_type=2: (2<<2)|0 = 0x08
        transport_codes = b"\x01\x02\x03\x04"
        # path_byte = 0x42 → mode=1, hop_count=2
        path_data = b"\xaa\xbb\xcc\xdd"
        payload = b"\xca\xfe"
        raw = bytes([0x08]) + transport_codes + bytes([0x42]) + path_data + payload

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert route == "F"
        assert path_values == ["aabb", "ccdd"]

    def test_truncated_multibyte_path_returns_defaults(self):
        """Packet claiming 2-byte hops but truncated → defaults."""
        # FLOOD route, payload_type=2: (2<<2)|1 = 0x09
        # path_byte = 0x42 → mode=1, hop_count=2 → needs 4 bytes, only 1
        raw = bytes([0x09, 0x42, 0xAA])

        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert path_values == []
        assert plen == "0"

    def test_reserved_mode_returns_defaults(self):
        raw = bytes([0x09, 0xC1, 0xAA, 0xBB, 0xCC])
        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert (route, ptype, plen, path_values, payload_type) == ("U", "0", "0", [], None)

    def test_oversize_path_len_returns_defaults(self):
        raw = bytes([0x09, 0xBF]) + bytes(189) + b"payload"
        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert (route, ptype, plen, path_values, payload_type) == ("U", "0", "0", [], None)

    def test_no_payload_returns_defaults(self):
        raw = bytes([0x09, 0x02, 0xAA, 0xBB])
        route, ptype, plen, path_values, payload_type = _decode_packet_fields(raw)
        assert (route, ptype, plen, path_values, payload_type) == ("U", "0", "0", [], None)


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
        pub._settings = SimpleNamespace(community_mqtt_enabled=False)
        with patch("app.keystore.get_public_key", return_value=b"x" * 32):
            assert pub._is_configured() is False

    def test_is_configured_false_when_no_private_key(self):
        pub = CommunityMqttPublisher()
        pub._settings = SimpleNamespace(
            community_mqtt_enabled=True, community_mqtt_auth_mode="token"
        )
        with (
            patch("app.keystore.get_public_key", return_value=b"x" * 32),
            patch("app.keystore.has_private_key", return_value=False),
        ):
            assert pub._is_configured() is False

    def test_is_configured_true_when_enabled_with_key(self):
        pub = CommunityMqttPublisher()
        pub._settings = SimpleNamespace(
            community_mqtt_enabled=True, community_mqtt_auth_mode="token"
        )
        with (
            patch("app.keystore.get_public_key", return_value=b"x" * 32),
            patch("app.keystore.has_private_key", return_value=True),
        ):
            assert pub._is_configured() is True

    def test_is_configured_true_for_none_auth_without_private_key(self):
        pub = CommunityMqttPublisher()
        pub._settings = SimpleNamespace(
            community_mqtt_enabled=True, community_mqtt_auth_mode="none"
        )
        with patch("app.keystore.get_public_key", return_value=b"x" * 32):
            assert pub._is_configured() is True


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


class TestBuildStatusTopic:
    def test_builds_correct_topic(self):
        settings = SimpleNamespace(community_mqtt_iata="LAX")
        topic = _build_status_topic(settings, "AABB1122")
        assert topic == "meshcore/LAX/AABB1122/status"

    def test_iata_uppercased_and_stripped(self):
        settings = SimpleNamespace(community_mqtt_iata=" lax ")
        topic = _build_status_topic(settings, "PUBKEY")
        assert topic == "meshcore/LAX/PUBKEY/status"


class TestRenderPacketTopic:
    def test_uses_default_template(self):
        topic = _render_packet_topic(
            "meshcore/{IATA}/{PUBLIC_KEY}/packets",
            iata="LAX",
            public_key="AABB1122",
        )
        assert topic == "meshcore/LAX/AABB1122/packets"

    def test_supports_custom_template(self):
        topic = _render_packet_topic(
            "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}",
            iata="PDX",
            public_key="PUBKEY",
        )
        assert topic == "mesh2mqtt/PDX/node/PUBKEY"

    def test_accepts_mixed_case_placeholders(self):
        topic = _render_packet_topic(
            "mesh2mqtt/{iata}/node/{Public_Key}",
            iata="SEA",
            public_key="PUBKEY",
        )
        assert topic == "mesh2mqtt/SEA/node/PUBKEY"


class TestCommunityConfigDefaults:
    def test_existing_config_without_new_fields_gets_letsmesh_defaults(self):
        settings = _config_to_settings({"iata": "LAX", "email": "user@example.com"})

        assert settings.community_mqtt_broker_host == "mqtt-us-v1.letsmesh.net"
        assert settings.community_mqtt_broker_port == 443
        assert settings.community_mqtt_transport == "websockets"
        assert settings.community_mqtt_use_tls is True
        assert settings.community_mqtt_tls_verify is True
        assert settings.community_mqtt_auth_mode == "token"
        assert settings.community_mqtt_username == ""
        assert settings.community_mqtt_password == ""
        assert settings.community_mqtt_token_audience == ""
        assert settings.community_mqtt_iata == "LAX"
        assert settings.community_mqtt_email == "user@example.com"


class TestLwtAndStatusPublish:
    def test_build_client_kwargs_includes_will(self):
        """_build_client_kwargs should return a will with offline status."""
        pub = CommunityMqttPublisher()
        private_key, public_key = _make_test_keys()
        pubkey_hex = public_key.hex().upper()
        settings = _make_community_settings(community_mqtt_iata="SFO")

        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {"name": "TestNode"}

        with (
            patch("app.keystore.get_private_key", return_value=private_key),
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
        ):
            kwargs = pub._build_client_kwargs(settings)

        assert "will" in kwargs
        will = kwargs["will"]
        assert will.topic == f"meshcore/SFO/{pubkey_hex}/status"
        assert will.retain is True
        payload = json.loads(will.payload)
        assert payload["status"] == "offline"
        assert payload["origin"] == "TestNode"
        assert payload["origin_id"] == pubkey_hex
        assert "timestamp" in payload
        assert "client" not in payload
        assert kwargs["transport"] == "websockets"
        assert kwargs["websocket_path"] == "/"
        assert kwargs["tls_context"] is not None
        assert kwargs["username"] == f"v1_{pubkey_hex}"

    def test_build_client_kwargs_supports_tcp_transport_and_custom_audience(self):
        pub = CommunityMqttPublisher()
        private_key, public_key = _make_test_keys()
        settings = _make_community_settings(
            community_mqtt_broker_host="meshrank.net",
            community_mqtt_broker_port=8883,
            community_mqtt_transport="tcp",
            community_mqtt_use_tls=True,
            community_mqtt_tls_verify=True,
            community_mqtt_token_audience="meshrank.net",
            community_mqtt_email="user@example.com",
            community_mqtt_iata="SFO",
        )

        with (
            patch("app.keystore.get_private_key", return_value=private_key),
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager") as mock_radio,
        ):
            mock_radio.backend = None
            kwargs = pub._build_client_kwargs(settings)

        assert kwargs["hostname"] == "meshrank.net"
        assert kwargs["port"] == 8883
        assert kwargs["transport"] == "tcp"
        assert "websocket_path" not in kwargs
        assert kwargs["tls_context"] is not None

        payload_b64 = kwargs["password"].split(".")[1]
        import base64

        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        assert payload["aud"] == "meshrank.net"
        assert payload["email"] == "user@example.com"

    def test_build_client_kwargs_supports_none_auth(self):
        pub = CommunityMqttPublisher()
        _, public_key = _make_test_keys()
        settings = _make_community_settings(
            community_mqtt_broker_host="meshrank.net",
            community_mqtt_broker_port=8883,
            community_mqtt_transport="tcp",
            community_mqtt_auth_mode="none",
        )

        with (
            patch("app.keystore.get_private_key", return_value=None),
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager") as mock_radio,
        ):
            mock_radio.backend = None
            kwargs = pub._build_client_kwargs(settings)

        assert kwargs["hostname"] == "meshrank.net"
        assert kwargs["port"] == 8883
        assert kwargs["transport"] == "tcp"
        assert "username" not in kwargs
        assert "password" not in kwargs

    @pytest.mark.asyncio
    async def test_on_connected_async_publishes_online_status(self):
        """_on_connected_async should publish a retained online status with enriched fields."""
        pub = CommunityMqttPublisher()
        private_key, public_key = _make_test_keys()
        pubkey_hex = public_key.hex().upper()
        settings = SimpleNamespace(
            community_mqtt_enabled=True,
            community_mqtt_iata="LAX",
        )

        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {"name": "TestNode"}

        with (
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
            patch.object(
                pub,
                "_fetch_device_info",
                new_callable=AsyncMock,
                return_value={"model": "T-Deck", "firmware_version": "v2.2.2 (Build: 2025-01-15)"},
            ),
            patch.object(
                pub, "_fetch_stats", new_callable=AsyncMock, return_value={"battery_mv": 4200}
            ),
            patch("app.fanout.community_mqtt._build_radio_info", return_value="915.0,250.0,10,8"),
            patch("app.fanout.community_mqtt._get_client_version", return_value="RemoteTerm 2.4.0"),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._on_connected_async(settings)

        mock_publish.assert_called_once()
        topic = mock_publish.call_args[0][0]
        payload = mock_publish.call_args[0][1]
        retain = mock_publish.call_args[1]["retain"]

        assert topic == f"meshcore/LAX/{pubkey_hex}/status"
        assert retain is True
        assert payload["status"] == "online"
        assert payload["origin"] == "TestNode"
        assert payload["origin_id"] == pubkey_hex
        assert "client" not in payload
        assert "timestamp" in payload
        assert payload["model"] == "T-Deck"
        assert payload["firmware_version"] == "v2.2.2 (Build: 2025-01-15)"
        assert payload["radio"] == "915.0,250.0,10,8"
        assert payload["client_version"] == "RemoteTerm 2.4.0"
        assert payload["stats"] == {"battery_mv": 4200}

    def test_lwt_and_online_share_same_topic(self):
        """LWT and on-connect status should use the same topic path."""
        pub = CommunityMqttPublisher()
        private_key, public_key = _make_test_keys()
        pubkey_hex = public_key.hex().upper()
        settings = _make_community_settings(community_mqtt_iata="JFK")

        mock_radio = MagicMock()
        mock_radio.backend = None

        with (
            patch("app.keystore.get_private_key", return_value=private_key),
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
        ):
            kwargs = pub._build_client_kwargs(settings)

        lwt_topic = kwargs["will"].topic
        expected_topic = _build_status_topic(settings, pubkey_hex)
        assert lwt_topic == expected_topic

    @pytest.mark.asyncio
    async def test_on_connected_async_skips_when_no_public_key(self):
        """_on_connected_async should no-op when public key is unavailable."""
        pub = CommunityMqttPublisher()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        with (
            patch("app.keystore.get_public_key", return_value=None),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._on_connected_async(settings)

        mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_connected_async_uses_fallback_device_name(self):
        """Should use 'MeshCore Device' when radio name is unavailable."""
        pub = CommunityMqttPublisher()
        _, public_key = _make_test_keys()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        mock_radio = MagicMock()
        mock_radio.backend = None

        with (
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
            patch.object(
                pub,
                "_fetch_device_info",
                new_callable=AsyncMock,
                return_value={"model": "unknown", "firmware_version": "unknown"},
            ),
            patch.object(pub, "_fetch_stats", new_callable=AsyncMock, return_value=None),
            patch("app.fanout.community_mqtt._build_radio_info", return_value="0,0,0,0"),
            patch(
                "app.fanout.community_mqtt._get_client_version", return_value="RemoteTerm unknown"
            ),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._on_connected_async(settings)

        payload = mock_publish.call_args[0][1]
        assert payload["origin"] == "MeshCore Device"


class TestCommunityPacketPublishTopic:
    @pytest.mark.asyncio
    async def test_publish_community_packet_uses_configured_topic_template(self):
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher.connected = True
        publisher._settings = object()

        data = {
            "id": 1,
            "observation_id": 1,
            "timestamp": 1700000000,
            "data": "0100",
            "payload_type": "GROUP_TEXT",
            "snr": None,
            "rssi": None,
            "decrypted": False,
            "decrypted_info": None,
        }
        config = {"iata": "lax", "topic_template": "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}"}

        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {"name": "Node"}

        with (
            patch("app.keystore.get_public_key", return_value=bytes.fromhex("AA" * 32)),
            patch("app.radio.radio_manager", mock_radio),
        ):
            await _publish_community_packet(publisher, config, data)

        publisher.publish.assert_awaited_once()
        topic = publisher.publish.await_args.args[0]
        assert topic == f"mesh2mqtt/LAX/node/{'AA' * 32}"


def _mock_radio_operation(mc_mock):
    """Create a mock async context manager for radio_operation."""

    @asynccontextmanager
    async def _op(*args, **kwargs):
        yield mc_mock

    return _op


class TestFetchDeviceInfo:
    @pytest.mark.asyncio
    async def test_success_fw_ver_3(self):
        """Should extract model and firmware_version from DEVICE_INFO with fw ver >= 3."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.send_device_query = AsyncMock(
            return_value=Event(
                EventType.DEVICE_INFO,
                {"fw ver": 3, "model": "T-Deck", "ver": "2.2.2", "fw_build": "2025-01-15"},
            )
        )

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_device_info()

        assert result["model"] == "T-Deck"
        assert result["firmware_version"] == "v2.2.2 (Build: 2025-01-15)"
        # Should be cached
        assert pub._cached_device_info == result

    @pytest.mark.asyncio
    async def test_fw_ver_below_3_caches_old_version(self):
        """Should cache old firmware version string when fw ver < 3."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.send_device_query = AsyncMock(
            return_value=Event(EventType.DEVICE_INFO, {"fw ver": 2})
        )

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_device_info()

        assert result["model"] == "unknown"
        assert result["firmware_version"] == "v2"
        # Should be cached (firmware doesn't change mid-connection)
        assert pub._cached_device_info == result

    @pytest.mark.asyncio
    async def test_error_returns_fallback_not_cached(self):
        """Should return unknowns when device query returns ERROR, without caching."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.send_device_query = AsyncMock(return_value=Event(EventType.ERROR, {}))

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_device_info()

        assert result["model"] == "unknown"
        assert result["firmware_version"] == "unknown"
        # Should NOT be cached — allows retry on next status publish
        assert pub._cached_device_info is None

    @pytest.mark.asyncio
    async def test_radio_busy_returns_fallback_not_cached(self):
        """Should return unknowns when radio is busy, without caching."""
        from app.radio import RadioOperationBusyError

        pub = CommunityMqttPublisher()

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = MagicMock(side_effect=RadioOperationBusyError("busy"))
            result = await pub._fetch_device_info()

        assert result["model"] == "unknown"
        assert result["firmware_version"] == "unknown"
        # Should NOT be cached — allows retry when radio becomes available
        assert pub._cached_device_info is None

    @pytest.mark.asyncio
    async def test_cached_result_returned_on_second_call(self):
        """Should return cached result without re-querying the radio."""
        pub = CommunityMqttPublisher()
        pub._cached_device_info = {"model": "T-Deck", "firmware_version": "v2.2.2"}

        # No radio mock needed — should return cached
        result = await pub._fetch_device_info()
        assert result["model"] == "T-Deck"

    @pytest.mark.asyncio
    async def test_no_fw_build_omits_build_suffix(self):
        """When fw_build is empty, firmware_version should just be 'vX.Y.Z'."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.send_device_query = AsyncMock(
            return_value=Event(
                EventType.DEVICE_INFO,
                {"fw ver": 3, "model": "Heltec", "ver": "1.0.0", "fw_build": ""},
            )
        )

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_device_info()

        assert result["firmware_version"] == "v1.0.0"


class TestFetchStats:
    @pytest.mark.asyncio
    async def test_success_merges_core_and_radio(self):
        """Should merge STATS_CORE and STATS_RADIO payloads."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.get_stats_core = AsyncMock(
            return_value=Event(
                EventType.STATS_CORE,
                {"battery_mv": 4200, "uptime_secs": 3600, "errors": 0, "queue_len": 0},
            )
        )
        mc_mock.get_stats_radio = AsyncMock(
            return_value=Event(
                EventType.STATS_RADIO,
                {
                    "noise_floor": -120,
                    "last_rssi": -85,
                    "last_snr": 10.5,
                    "tx_air_secs": 42,
                    "rx_air_secs": 150,
                },
            )
        )

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_stats()

        assert result is not None
        assert result["battery_mv"] == 4200
        assert result["noise_floor"] == -120
        assert result["tx_air_secs"] == 42

    @pytest.mark.asyncio
    async def test_core_error_sets_stats_unsupported(self):
        """Should set _stats_supported=False when STATS_CORE returns ERROR."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.get_stats_core = AsyncMock(return_value=Event(EventType.ERROR, {}))

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            result = await pub._fetch_stats()

        assert pub._stats_supported is False
        assert result is None  # no cached stats yet

    @pytest.mark.asyncio
    async def test_radio_error_sets_stats_unsupported(self):
        """Should set _stats_supported=False when STATS_RADIO returns ERROR."""
        from meshcore.events import Event, EventType

        pub = CommunityMqttPublisher()
        mc_mock = MagicMock()
        mc_mock.get_stats_core = AsyncMock(
            return_value=Event(
                EventType.STATS_CORE,
                {"battery_mv": 4200, "uptime_secs": 3600, "errors": 0, "queue_len": 0},
            )
        )
        mc_mock.get_stats_radio = AsyncMock(return_value=Event(EventType.ERROR, {}))

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            await pub._fetch_stats()

        assert pub._stats_supported is False

    @pytest.mark.asyncio
    async def test_stats_unsupported_skips_radio(self):
        """When _stats_supported=False, should return cached stats without radio call."""
        pub = CommunityMqttPublisher()
        pub._stats_supported = False
        pub._cached_stats = {"battery_mv": 4000}

        result = await pub._fetch_stats()
        assert result == {"battery_mv": 4000}

    @pytest.mark.asyncio
    async def test_cache_guard_prevents_refetch(self):
        """Should return cached stats when within cache window."""
        pub = CommunityMqttPublisher()
        pub._cached_stats = {"battery_mv": 4200}
        pub._last_stats_fetch = time.monotonic()  # Just fetched

        result = await pub._fetch_stats()
        assert result == {"battery_mv": 4200}

    @pytest.mark.asyncio
    async def test_radio_busy_returns_cached(self):
        """Should return cached stats when radio is busy."""
        from app.radio import RadioOperationBusyError

        pub = CommunityMqttPublisher()
        pub._cached_stats = {"battery_mv": 3900}

        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = MagicMock(side_effect=RadioOperationBusyError("busy"))
            result = await pub._fetch_stats()

        assert result == {"battery_mv": 3900}


class TestBuildRadioInfo:
    def test_formatted_string(self):
        """Should return comma-separated radio info matching reference format."""
        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {
            "radio_freq": 915.0,
            "radio_bw": 250.0,
            "radio_sf": 10,
            "radio_cr": 8,
        }

        with patch("app.radio.radio_manager", mock_radio):
            result = _build_radio_info()

        assert result == "915.0,250.0,10,8"

    def test_fallback_when_no_meshcore(self):
        """Should return '0,0,0,0' when meshcore is None."""
        mock_radio = MagicMock()
        mock_radio.backend = None

        with patch("app.radio.radio_manager", mock_radio):
            result = _build_radio_info()

        assert result == "0,0,0,0"

    def test_fallback_when_self_info_missing_fields(self):
        """Should use 0 defaults when self_info lacks radio fields."""
        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {"name": "TestNode"}

        with patch("app.radio.radio_manager", mock_radio):
            result = _build_radio_info()

        assert result == "0,0,0,0"

    def test_uses_spi_style_radio_fields(self):
        """SPI backend exposes canonical radio field names; exporter should honor them."""
        mock_backend = MagicMock()
        mock_backend.self_info = {
            "frequency": 910525000,
            "bandwidth": 62500,
            "spreading_factor": 7,
            "coding_rate": 5,
        }
        mock_runtime = MagicMock()
        mock_runtime.backend = mock_backend

        with patch("app.services.radio_runtime.radio_runtime", mock_runtime):
            result = _build_radio_info()

        assert result == "910525000,62500,7,5"


class TestGetClientVersion:
    def test_returns_remoteterm_prefix(self):
        """Should return 'RemoteTerm ...' string."""
        result = _get_client_version()
        assert result.startswith("RemoteTerm ")

    def test_returns_version_from_metadata(self):
        """Should use importlib.metadata to get version."""
        with patch("app.fanout.community_mqtt.importlib.metadata.version", return_value="1.2.3"):
            result = _get_client_version()
        assert result == "RemoteTerm 1.2.3"

    def test_fallback_on_error(self):
        """Should return 'RemoteTerm unknown' if metadata lookup fails."""
        with patch(
            "app.fanout.community_mqtt.importlib.metadata.version",
            side_effect=Exception("not found"),
        ):
            result = _get_client_version()
        assert result == "RemoteTerm unknown"


class TestPublishStatus:
    @pytest.mark.asyncio
    async def test_enriched_payload_fields(self):
        """_publish_status should include all enriched fields."""
        pub = CommunityMqttPublisher()
        _, public_key = _make_test_keys()
        pubkey_hex = public_key.hex().upper()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        mock_radio = MagicMock()
        mock_radio.backend = MagicMock()
        mock_radio.backend.self_info = {"name": "TestNode"}

        stats = {"battery_mv": 4200, "uptime_secs": 3600, "noise_floor": -120}

        with (
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
            patch.object(
                pub,
                "_fetch_device_info",
                new_callable=AsyncMock,
                return_value={"model": "T-Deck", "firmware_version": "v2.2.2 (Build: 2025-01-15)"},
            ),
            patch.object(pub, "_fetch_stats", new_callable=AsyncMock, return_value=stats),
            patch("app.fanout.community_mqtt._build_radio_info", return_value="915.0,250.0,10,8"),
            patch("app.fanout.community_mqtt._get_client_version", return_value="RemoteTerm 2.4.0"),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._publish_status(settings)

        payload = mock_publish.call_args[0][1]
        assert payload["status"] == "online"
        assert payload["origin"] == "TestNode"
        assert payload["origin_id"] == pubkey_hex
        assert "client" not in payload
        assert payload["model"] == "T-Deck"
        assert payload["firmware_version"] == "v2.2.2 (Build: 2025-01-15)"
        assert payload["radio"] == "915.0,250.0,10,8"
        assert payload["client_version"] == "RemoteTerm 2.4.0"
        assert payload["stats"] == stats

    @pytest.mark.asyncio
    async def test_stats_omitted_when_none(self):
        """Should not include 'stats' key when stats are None."""
        pub = CommunityMqttPublisher()
        _, public_key = _make_test_keys()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        mock_radio = MagicMock()
        mock_radio.backend = None

        with (
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
            patch.object(
                pub,
                "_fetch_device_info",
                new_callable=AsyncMock,
                return_value={"model": "unknown", "firmware_version": "unknown"},
            ),
            patch.object(pub, "_fetch_stats", new_callable=AsyncMock, return_value=None),
            patch("app.fanout.community_mqtt._build_radio_info", return_value="0,0,0,0"),
            patch(
                "app.fanout.community_mqtt._get_client_version", return_value="RemoteTerm unknown"
            ),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._publish_status(settings)

        payload = mock_publish.call_args[0][1]
        assert "stats" not in payload

    @pytest.mark.asyncio
    async def test_updates_last_status_publish(self):
        """Should update _last_status_publish after publishing."""
        pub = CommunityMqttPublisher()
        _, public_key = _make_test_keys()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        mock_radio = MagicMock()
        mock_radio.backend = None

        before = time.monotonic()

        with (
            patch("app.keystore.get_public_key", return_value=public_key),
            patch("app.radio.radio_manager", mock_radio),
            patch.object(
                pub,
                "_fetch_device_info",
                new_callable=AsyncMock,
                return_value={"model": "unknown", "firmware_version": "unknown"},
            ),
            patch.object(pub, "_fetch_stats", new_callable=AsyncMock, return_value=None),
            patch("app.fanout.community_mqtt._build_radio_info", return_value="0,0,0,0"),
            patch(
                "app.fanout.community_mqtt._get_client_version", return_value="RemoteTerm unknown"
            ),
            patch.object(pub, "publish", new_callable=AsyncMock),
        ):
            await pub._publish_status(settings)

        assert pub._last_status_publish >= before

    @pytest.mark.asyncio
    async def test_no_publish_key_returns_none(self):
        """Should skip publish when public key is unavailable."""
        pub = CommunityMqttPublisher()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")

        with (
            patch("app.keystore.get_public_key", return_value=None),
            patch.object(pub, "publish", new_callable=AsyncMock) as mock_publish,
        ):
            await pub._publish_status(settings)

        mock_publish.assert_not_called()


class TestPeriodicWake:
    @pytest.mark.asyncio
    async def test_skips_before_interval(self):
        """Should not republish before _STATS_REFRESH_INTERVAL."""
        pub = CommunityMqttPublisher()
        pub._settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")
        pub._last_status_publish = time.monotonic()  # Just published

        with patch.object(pub, "_publish_status", new_callable=AsyncMock) as mock_ps:
            await pub._on_periodic_wake(60.0)

        mock_ps.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_after_interval(self):
        """Should republish after _STATS_REFRESH_INTERVAL elapsed."""
        pub = CommunityMqttPublisher()
        pub._settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="LAX")
        pub._last_status_publish = time.monotonic() - _STATS_REFRESH_INTERVAL - 1

        with patch.object(pub, "_publish_status", new_callable=AsyncMock) as mock_ps:
            await pub._on_periodic_wake(360.0)

        mock_ps.assert_called_once_with(pub._settings, refresh_stats=True)

    @pytest.mark.asyncio
    async def test_skips_when_no_settings(self):
        """Should no-op when settings are None."""
        pub = CommunityMqttPublisher()
        pub._settings = None

        with patch.object(pub, "_publish_status", new_callable=AsyncMock) as mock_ps:
            await pub._on_periodic_wake(360.0)

        mock_ps.assert_not_called()
