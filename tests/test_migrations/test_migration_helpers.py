"""Tests for database migration(s)."""


class TestMigrationPacketHelpers:
    """Test migration-local packet helpers against canonical path validation."""

    def test_extract_payload_for_hash_rejects_oversize_path(self):
        from app.migrations._005_backfill_payload_hashes import _extract_payload_for_hash

        packet = bytes([0x15, 0xBF]) + bytes(189) + b"payload"
        assert _extract_payload_for_hash(packet) is None

    def test_extract_payload_for_hash_rejects_no_payload_packet(self):
        from app.migrations._005_backfill_payload_hashes import _extract_payload_for_hash

        packet = bytes([0x15, 0x02, 0xAA, 0xBB])
        assert _extract_payload_for_hash(packet) is None

    def test_extract_path_from_packet_rejects_reserved_mode(self):
        from app.migrations._007_backfill_message_paths import _extract_path_from_packet

        packet = bytes([0x15, 0xC1, 0xAA, 0xBB, 0xCC])
        assert _extract_path_from_packet(packet) is None
