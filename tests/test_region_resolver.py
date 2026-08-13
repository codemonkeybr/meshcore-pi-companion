"""Tests for regional flood-scope (transport code) resolution."""

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.path_utils import parse_packet_envelope
from app.region_resolver import compute_transport_code, resolve_region

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "websocket_events.json"
with open(FIXTURES_PATH) as f:
    FIXTURES = json.load(f)


def _reference_code(region_name: str, payload_type: int, payload: bytes) -> int:
    """Independent reimplementation of the firmware algorithm for cross-checking."""
    key = hashlib.sha256(("#" + region_name).encode()).digest()[:16]
    digest = hmac.new(key, bytes([payload_type]) + payload, hashlib.sha256).digest()
    code = int.from_bytes(digest[:2], "little")
    if code == 0:
        return 1
    if code == 0xFFFF:
        return 0xFFFE
    return code


class TestComputeTransportCode:
    def test_matches_reference_algorithm(self):
        payload = bytes.fromhex("0badcafe1234")
        assert compute_transport_code("nl-gr", 0x05, payload) == _reference_code(
            "nl-gr", 0x05, payload
        )

    def test_hashtag_prefix_is_equivalent(self):
        payload = b"hello"
        assert compute_transport_code("nl-gr", 0x05, payload) == compute_transport_code(
            "#nl-gr", 0x05, payload
        )

    def test_blank_region_returns_none(self):
        assert compute_transport_code("", 0x05, b"x") is None

    def test_code_depends_on_payload(self):
        # The code is a keyed MAC over the payload, so different payloads under the
        # same region produce different codes (this is why there is no static map).
        assert compute_transport_code("nl-gr", 0x05, b"a") != compute_transport_code(
            "nl-gr", 0x05, b"b"
        )

    def test_never_returns_reserved_values(self):
        for i in range(3000):
            code = compute_transport_code(f"region-{i}", 0x05, bytes([i & 0xFF, (i >> 8) & 0xFF]))
            assert code not in (0x0000, 0xFFFF)


class TestResolveRegion:
    def test_resolves_first_matching_region(self):
        payload = bytes.fromhex("c0ffee")
        code = compute_transport_code("nl-gr", 0x05, payload)
        assert resolve_region(0x05, payload, code, ["de-by", "nl-gr", "fr"]) == "nl-gr"

    def test_no_match_returns_none(self):
        payload = bytes.fromhex("c0ffee")
        code = compute_transport_code("nl-gr", 0x05, payload)
        assert resolve_region(0x05, payload, code, ["de-by", "fr"]) is None

    def test_empty_candidates_returns_none(self):
        assert resolve_region(0x05, b"x", 0x1234, []) is None

    def test_blank_candidate_names_skipped(self):
        payload = b"x"
        code = compute_transport_code("nl-gr", 0x05, payload)
        assert resolve_region(0x05, payload, code, ["", "nl-gr"]) == "nl-gr"


class TestEnvelopeTransportCodes:
    def test_flood_packet_has_no_transport_codes(self):
        raw = bytes.fromhex(FIXTURES["channel_message"]["raw_packet_hex"])
        env = parse_packet_envelope(raw)
        assert env is not None
        assert env.transport_codes is None

    def test_transport_routed_packet_exposes_codes(self):
        # Build a TRANSPORT_FLOOD packet: header | code_1 | code_2 | path_byte | payload
        code_1, code_2 = 0x9164, 0x0000
        header = bytes([0x05 << 2])  # payload_type=GROUP_TEXT, route_type=TRANSPORT_FLOOD(0)
        raw = (
            header
            + code_1.to_bytes(2, "little")
            + code_2.to_bytes(2, "little")
            + bytes([0x00])  # path byte: 0 hops, 1-byte hash
            + b"payloadbytes"
        )
        env = parse_packet_envelope(raw)
        assert env is not None
        assert env.transport_codes == (code_1, code_2)
        assert env.payload == b"payloadbytes"


def _build_transport_channel_packet(region_name: str | None, code_override: int | None = None):
    """Rebuild the channel fixture as a TRANSPORT_FLOOD packet, scoped to a region."""
    raw = bytes.fromhex(FIXTURES["channel_message"]["raw_packet_hex"])
    env = parse_packet_envelope(raw)
    assert env is not None and env.hop_count == 0
    payload = env.payload
    if code_override is not None:
        code_1 = code_override
    else:
        code_1 = compute_transport_code(region_name, 0x05, payload)
    header = bytes([0x05 << 2])  # GROUP_TEXT + TRANSPORT_FLOOD
    return (
        header + code_1.to_bytes(2, "little") + (0).to_bytes(2, "little") + bytes([0x00]) + payload
    ), code_1


class TestRegionPersistedOnChannelMessage:
    @pytest.mark.asyncio
    async def test_known_region_stored_on_message_and_broadcast(self, test_db, captured_broadcasts):
        from app.packet_processor import process_raw_packet
        from app.repository import AppSettingsRepository, ChannelRepository, MessageRepository

        fixture = FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(), name=fixture["channel_name"], is_hashtag=True
        )
        await AppSettingsRepository.update(known_regions=["nl-gr"])

        packet_bytes, code = _build_transport_channel_packet("nl-gr")
        broadcasts, mock_broadcast = captured_broadcasts
        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await process_raw_packet(packet_bytes, timestamp=1700000000)

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=fixture["channel_key_hex"].upper(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].region == "nl-gr"
        assert messages[0].transport_code == code

        # WS message + raw_packet broadcasts both carry the region
        msg_b = [b for b in broadcasts if b["type"] == "message"][0]
        assert msg_b["data"]["region"] == "nl-gr"
        assert msg_b["data"]["transport_code"] == code
        raw_b = [b for b in broadcasts if b["type"] == "raw_packet"][0]
        assert raw_b["data"]["region"] == "nl-gr"
        assert raw_b["data"]["transport_code"] == code

    @pytest.mark.asyncio
    async def test_unlisted_region_keeps_code_but_no_name(self, test_db, captured_broadcasts):
        from app.packet_processor import process_raw_packet
        from app.repository import AppSettingsRepository, ChannelRepository, MessageRepository

        fixture = FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(), name=fixture["channel_name"], is_hashtag=True
        )
        # Region list does NOT include the scope this packet is tagged with.
        await AppSettingsRepository.update(known_regions=["somewhere-else"])

        packet_bytes, code = _build_transport_channel_packet("nl-gr")
        _, mock_broadcast = captured_broadcasts
        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await process_raw_packet(packet_bytes, timestamp=1700000000)

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=fixture["channel_key_hex"].upper(), limit=10
        )
        assert len(messages) == 1
        # Scoped (transport_code set) but region unknown → distinguishable from unscoped.
        assert messages[0].transport_code == code
        assert messages[0].region is None

    @pytest.mark.asyncio
    async def test_backfill_tags_messages_ingested_before_region_was_known(
        self, test_db, captured_broadcasts
    ):
        from app.packet_processor import process_raw_packet
        from app.repository import AppSettingsRepository, ChannelRepository, MessageRepository
        from app.services.messages import backfill_message_regions

        fixture = FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(), name=fixture["channel_name"], is_hashtag=True
        )
        # Ingest a region-scoped message while the region is NOT yet in the list.
        await AppSettingsRepository.update(known_regions=[])
        packet_bytes, code = _build_transport_channel_packet("nl-gr")
        _, mock_broadcast = captured_broadcasts
        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await process_raw_packet(packet_bytes, timestamp=1700000000)

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=fixture["channel_key_hex"].upper(), limit=10
        )
        assert len(messages) == 1
        # transport_code is set at ingest, but region is unresolved (empty list).
        assert messages[0].transport_code == code
        assert messages[0].region is None

        # Operator adds the region and runs the backfill.
        result = await backfill_message_regions(["nl-gr"])
        assert result["named"] == 1

        refreshed = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=fixture["channel_key_hex"].upper(), limit=10
        )
        assert refreshed[0].region == "nl-gr"
        assert refreshed[0].transport_code == code

    @pytest.mark.asyncio
    async def test_plain_flood_message_is_unscoped(self, test_db, captured_broadcasts):
        from app.packet_processor import process_raw_packet
        from app.repository import AppSettingsRepository, ChannelRepository, MessageRepository

        fixture = FIXTURES["channel_message"]
        await ChannelRepository.upsert(
            key=fixture["channel_key_hex"].upper(), name=fixture["channel_name"], is_hashtag=True
        )
        await AppSettingsRepository.update(known_regions=["nl-gr"])

        packet_bytes = bytes.fromhex(fixture["raw_packet_hex"])  # original FLOOD packet
        _, mock_broadcast = captured_broadcasts
        with patch("app.packet_processor.broadcast_event", mock_broadcast):
            await process_raw_packet(packet_bytes, timestamp=1700000000)

        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=fixture["channel_key_hex"].upper(), limit=10
        )
        assert len(messages) == 1
        assert messages[0].transport_code is None
        assert messages[0].region is None
