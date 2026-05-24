"""Tests for the centralized path encoding/decoding helpers."""

import pytest

from app.path_utils import (
    decode_path_byte,
    first_hop_hex,
    normalize_contact_route,
    normalize_route_override,
    parse_explicit_hop_route,
    parse_packet_envelope,
    path_wire_len,
    split_path_hex,
    validate_path_byte,
)


class TestDecodePathByte:
    """Test decoding the packed [hash_mode:2][hop_count:6] byte."""

    def test_mode0_single_hop(self):
        """Mode 0 (1-byte hops), 1 hop → path_byte = 0x01."""
        hop_count, hash_size = decode_path_byte(0x01)
        assert hop_count == 1
        assert hash_size == 1

    def test_mode0_three_hops(self):
        """Mode 0, 3 hops → path_byte = 0x03."""
        hop_count, hash_size = decode_path_byte(0x03)
        assert hop_count == 3
        assert hash_size == 1

    def test_mode0_zero_hops(self):
        """Mode 0, 0 hops (direct) → path_byte = 0x00."""
        hop_count, hash_size = decode_path_byte(0x00)
        assert hop_count == 0
        assert hash_size == 1

    def test_mode1_two_byte_hops(self):
        """Mode 1 (2-byte hops), 2 hops → path_byte = 0x42."""
        hop_count, hash_size = decode_path_byte(0x42)
        assert hop_count == 2
        assert hash_size == 2

    def test_mode1_single_hop(self):
        """Mode 1 (2-byte hops), 1 hop → path_byte = 0x41."""
        hop_count, hash_size = decode_path_byte(0x41)
        assert hop_count == 1
        assert hash_size == 2

    def test_mode2_three_byte_hops(self):
        """Mode 2 (3-byte hops), 1 hop → path_byte = 0x81."""
        hop_count, hash_size = decode_path_byte(0x81)
        assert hop_count == 1
        assert hash_size == 3

    def test_mode2_max_hops(self):
        """Mode 2, 63 hops (maximum) → path_byte = 0xBF."""
        hop_count, hash_size = decode_path_byte(0xBF)
        assert hop_count == 63
        assert hash_size == 3

    def test_mode3_reserved_raises(self):
        """Mode 3 is reserved and should raise ValueError."""
        with pytest.raises(ValueError, match="Reserved path hash mode 3"):
            decode_path_byte(0xC0)

    def test_mode3_with_hops_raises(self):
        """Mode 3 with hop count should also raise."""
        with pytest.raises(ValueError, match="Reserved"):
            decode_path_byte(0xC5)

    def test_backward_compat_old_firmware(self):
        """Old firmware packets have upper bits = 0, so mode=0 and path_byte = hop count."""
        for n in range(0, 64):
            hop_count, hash_size = decode_path_byte(n)
            assert hop_count == n
            assert hash_size == 1


class TestPathWireLen:
    def test_basic(self):
        assert path_wire_len(3, 1) == 3
        assert path_wire_len(2, 2) == 4
        assert path_wire_len(1, 3) == 3
        assert path_wire_len(0, 1) == 0


class TestValidatePathByte:
    def test_accepts_valid_multibyte_path_len(self):
        hop_count, hash_size, byte_len = validate_path_byte(0x42)
        assert (hop_count, hash_size, byte_len) == (2, 2, 4)

    def test_rejects_oversize_path(self):
        with pytest.raises(ValueError, match="MAX_PATH_SIZE"):
            validate_path_byte(0xBF)


class TestParsePacketEnvelope:
    def test_parses_valid_packet(self):
        envelope = parse_packet_envelope(bytes([0x15, 0x42, 0xAA, 0xBB, 0xCC, 0xDD]) + b"hi")
        assert envelope is not None
        assert envelope.hop_count == 2
        assert envelope.hash_size == 2
        assert envelope.path == bytes([0xAA, 0xBB, 0xCC, 0xDD])
        assert envelope.payload == b"hi"

    def test_rejects_packet_with_no_payload(self):
        assert parse_packet_envelope(bytes([0x15, 0x02, 0xAA, 0xBB])) is None

    def test_rejects_oversize_path_encoding(self):
        packet = bytes([0x15, 0xBF]) + bytes(189) + b"x"
        assert parse_packet_envelope(packet) is None


class TestSplitPathHex:
    def test_one_byte_hops(self):
        assert split_path_hex("1a2b3c", 3) == ["1a", "2b", "3c"]

    def test_two_byte_hops(self):
        assert split_path_hex("1a2b3c4d", 2) == ["1a2b", "3c4d"]

    def test_three_byte_hops(self):
        assert split_path_hex("1a2b3c4d5e6f", 2) == ["1a2b3c", "4d5e6f"]

    def test_empty_path(self):
        assert split_path_hex("", 0) == []
        assert split_path_hex("", 3) == []

    def test_zero_hop_count(self):
        assert split_path_hex("1a2b", 0) == []

    def test_inconsistent_length_falls_back(self):
        """If hex length doesn't divide evenly by hop_count, fall back to 2-char chunks."""
        assert split_path_hex("1a2b3c", 2) == ["1a", "2b", "3c"]

    def test_single_hop_one_byte(self):
        assert split_path_hex("ab", 1) == ["ab"]

    def test_single_hop_two_bytes(self):
        assert split_path_hex("abcd", 1) == ["abcd"]


class TestFirstHopHex:
    def test_one_byte_hops(self):
        assert first_hop_hex("1a2b3c", 3) == "1a"

    def test_two_byte_hops(self):
        assert first_hop_hex("1a2b3c4d", 2) == "1a2b"

    def test_empty(self):
        assert first_hop_hex("", 0) is None
        assert first_hop_hex("", 1) is None

    def test_direct_path(self):
        assert first_hop_hex("", 0) is None


class TestNormalizeContactRoute:
    def test_decodes_legacy_signed_packed_len(self):
        path_hex, path_len, hash_mode = normalize_contact_route("3f3f69de1c7b7e7662", -125, 2)
        assert path_hex == "3f3f69de1c7b7e7662"
        assert path_len == 3
        assert hash_mode == 2

    def test_decodes_legacy_unsigned_packed_len(self):
        path_hex, path_len, hash_mode = normalize_contact_route("7e7662ae9258", 130, None)
        assert path_hex == "7e7662ae9258"
        assert path_len == 2
        assert hash_mode == 2

    def test_normalizes_flood_to_empty_path(self):
        path_hex, path_len, hash_mode = normalize_contact_route("abcd", -1, 2)
        assert path_hex == ""
        assert path_len == -1
        assert hash_mode == -1


class TestNormalizeRouteOverride:
    def test_preserves_unset_override(self):
        assert normalize_route_override(None, None, None) == (None, None, None)

    def test_normalizes_forced_direct_override(self):
        path_hex, path_len, hash_mode = normalize_route_override(None, 0, None)
        assert path_hex == ""
        assert path_len == 0
        assert hash_mode == 0


class TestParseExplicitHopRoute:
    def test_parses_one_byte_hops(self):
        assert parse_explicit_hop_route("ae,f1") == ("aef1", 2, 0)

    def test_parses_two_byte_hops(self):
        assert parse_explicit_hop_route("ae92,f13e") == ("ae92f13e", 2, 1)

    def test_rejects_mixed_width_hops(self):
        with pytest.raises(ValueError, match="same width"):
            parse_explicit_hop_route("ae,f13e")


class TestContactToRadioDictHashMode:
    """Test that Contact.to_radio_dict() preserves the stored direct-route hash mode."""

    def test_preserves_1byte_mode(self):
        from app.models import Contact

        c = Contact(
            public_key="aa" * 32,
            direct_path="1a2b3c",
            direct_path_len=3,
            direct_path_hash_mode=0,
        )
        d = c.to_radio_dict()
        assert d["out_path_hash_mode"] == 0

    def test_preserves_2byte_mode(self):
        from app.models import Contact

        c = Contact(
            public_key="bb" * 32,
            direct_path="1a2b3c4d",
            direct_path_len=2,
            direct_path_hash_mode=1,
        )
        d = c.to_radio_dict()
        assert d["out_path_hash_mode"] == 1

    def test_preserves_3byte_mode(self):
        from app.models import Contact

        c = Contact(
            public_key="cc" * 32,
            direct_path="1a2b3c4d5e6f",
            direct_path_len=2,
            direct_path_hash_mode=2,
        )
        d = c.to_radio_dict()
        assert d["out_path_hash_mode"] == 2

    def test_preserves_flood_mode(self):
        from app.models import Contact

        c = Contact(
            public_key="dd" * 32,
            direct_path=None,
            direct_path_len=-1,
            direct_path_hash_mode=-1,
        )
        d = c.to_radio_dict()
        assert d["out_path_hash_mode"] == -1

    def test_preserves_mode_with_zero_bytes_in_path(self):
        from app.models import Contact

        c = Contact(
            public_key="ee" * 32,
            direct_path="aa00bb00",
            direct_path_len=2,
            direct_path_hash_mode=1,
        )
        d = c.to_radio_dict()
        assert d["out_path_hash_mode"] == 1

    def test_decodes_legacy_signed_packed_len_before_radio_sync(self):
        from app.models import Contact

        c = Contact(
            public_key="ff" * 32,
            direct_path="3f3f69de1c7b7e7662",
            direct_path_len=-125,
            direct_path_hash_mode=2,
        )
        d = c.to_radio_dict()
        assert d["out_path"] == "3f3f69de1c7b7e7662"
        assert d["out_path_len"] == 3
        assert d["out_path_hash_mode"] == 2

    def test_route_override_takes_precedence_over_learned_route(self):
        from app.models import Contact

        c = Contact(
            public_key="11" * 32,
            direct_path="aabb",
            direct_path_len=1,
            direct_path_hash_mode=0,
            route_override_path="cc00dd00",
            route_override_len=2,
            route_override_hash_mode=1,
        )
        d = c.to_radio_dict()
        assert d["out_path"] == "cc00dd00"
        assert d["out_path_len"] == 2
        assert d["out_path_hash_mode"] == 1


class TestContactFromRadioDictHashMode:
    """Test that ContactUpsert.from_radio_dict() preserves explicit path hash mode."""

    def test_preserves_mode_from_radio_payload(self):
        from app.models import ContactUpsert

        upsert = ContactUpsert.from_radio_dict(
            "aa" * 32,
            {
                "adv_name": "Alice",
                "out_path": "aa00bb00",
                "out_path_len": 2,
                "out_path_hash_mode": 1,
            },
        )
        assert upsert.direct_path == "aa00bb00"
        assert upsert.direct_path_len == 2
        assert upsert.direct_path_hash_mode == 1

    def test_flood_falls_back_to_minus_one(self):
        from app.models import ContactUpsert

        upsert = ContactUpsert.from_radio_dict(
            "bb" * 32,
            {
                "adv_name": "Bob",
                "out_path": "",
                "out_path_len": -1,
            },
        )
        assert upsert.direct_path == ""
        assert upsert.direct_path_len == -1
        assert upsert.direct_path_hash_mode == -1
