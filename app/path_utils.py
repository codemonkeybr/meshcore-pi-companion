"""
Centralized helpers for MeshCore multi-byte path encoding.

The path_len wire byte is packed as [hash_mode:2][hop_count:6]:
  - hash_size = (hash_mode) + 1  →  1, 2, or 3 bytes per hop
  - hop_count = lower 6 bits     →  0–63 hops
  - wire bytes = hop_count × hash_size

Mode 3 (hash_size=4) is reserved and rejected.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

MAX_PATH_SIZE = 64


@dataclass(frozen=True)
class ParsedPacketEnvelope:
    """Canonical packet framing parse matching MeshCore Packet::readFrom()."""

    header: int
    route_type: int
    payload_type: int
    payload_version: int
    path_byte: int
    hop_count: int
    hash_size: int
    path_byte_len: int
    path: bytes
    payload: bytes
    payload_offset: int
    transport_codes: tuple[int, int] | None = None
    """Region transport codes (code_1, code_2) for TRANSPORT_* routes, else None.

    Each is a little-endian uint16 read from the 4-byte transport-code block.
    ``code_1`` is the region-scope code; ``code_2`` is currently reserved (0).
    """


def decode_path_byte(path_byte: int) -> tuple[int, int]:
    """Decode a packed path byte into (hop_count, hash_size).

    Returns:
        (hop_count, hash_size) where hash_size is 1, 2, or 3.

    Raises:
        ValueError: If hash_mode is 3 (reserved).
    """
    hash_mode = (path_byte >> 6) & 0x03
    if hash_mode == 3:
        raise ValueError(f"Reserved path hash mode 3 (path_byte=0x{path_byte:02X})")
    hop_count = path_byte & 0x3F
    hash_size = hash_mode + 1
    return hop_count, hash_size


def path_wire_len(hop_count: int, hash_size: int) -> int:
    """Wire byte length of path data."""
    return hop_count * hash_size


def validate_path_byte(path_byte: int) -> tuple[int, int, int]:
    """Validate a packed path byte using firmware-equivalent rules.

    Returns:
        (hop_count, hash_size, byte_len)

    Raises:
        ValueError: If the encoding uses reserved mode 3 or exceeds MAX_PATH_SIZE.
    """
    hop_count, hash_size = decode_path_byte(path_byte)
    byte_len = path_wire_len(hop_count, hash_size)
    if byte_len > MAX_PATH_SIZE:
        raise ValueError(
            f"Invalid path length {byte_len} bytes exceeds MAX_PATH_SIZE={MAX_PATH_SIZE}"
        )
    return hop_count, hash_size, byte_len


def parse_packet_envelope(raw_packet: bytes) -> ParsedPacketEnvelope | None:
    """Parse packet framing using firmware Packet::readFrom() semantics.

    Validation matches the firmware's path checks:
    - reserved mode 3 is invalid
    - hop_count * hash_size must not exceed MAX_PATH_SIZE
    - at least one payload byte must remain after the path
    """
    if len(raw_packet) < 2:
        return None

    try:
        header = raw_packet[0]
        route_type = header & 0x03
        payload_type = (header >> 2) & 0x0F
        payload_version = (header >> 6) & 0x03

        offset = 1
        transport_codes: tuple[int, int] | None = None
        if route_type in (0x00, 0x03):
            if len(raw_packet) < offset + 4:
                return None
            transport_codes = (
                int.from_bytes(raw_packet[offset : offset + 2], "little"),
                int.from_bytes(raw_packet[offset + 2 : offset + 4], "little"),
            )
            offset += 4

        if len(raw_packet) < offset + 1:
            return None
        path_byte = raw_packet[offset]
        offset += 1

        hop_count, hash_size, path_byte_len = validate_path_byte(path_byte)
        if len(raw_packet) < offset + path_byte_len:
            return None

        path = raw_packet[offset : offset + path_byte_len]
        offset += path_byte_len

        if offset >= len(raw_packet):
            return None

        return ParsedPacketEnvelope(
            header=header,
            route_type=route_type,
            payload_type=payload_type,
            payload_version=payload_version,
            path_byte=path_byte,
            hop_count=hop_count,
            hash_size=hash_size,
            path_byte_len=path_byte_len,
            path=path,
            payload=raw_packet[offset:],
            payload_offset=offset,
            transport_codes=transport_codes,
        )
    except (IndexError, ValueError):
        return None


def split_path_hex(path_hex: str, hop_count: int) -> list[str]:
    """Split a hex path string into per-hop chunks using the known hop count.

    If hop_count is 0 or the hex length doesn't divide evenly, falls back
    to 2-char (1-byte) chunks for backward compatibility.
    """
    if not path_hex or hop_count <= 0:
        return []
    chars_per_hop = len(path_hex) // hop_count
    if chars_per_hop < 2 or chars_per_hop % 2 != 0 or chars_per_hop * hop_count != len(path_hex):
        # Inconsistent — fall back to legacy 2-char split
        return [path_hex[i : i + 2] for i in range(0, len(path_hex), 2)]
    return [path_hex[i : i + chars_per_hop] for i in range(0, len(path_hex), chars_per_hop)]


def first_hop_hex(path_hex: str, hop_count: int) -> str | None:
    """Extract the first hop identifier from a path hex string.

    Returns None for empty/direct paths.
    """
    hops = split_path_hex(path_hex, hop_count)
    return hops[0] if hops else None


def normalize_contact_route(
    path_hex: str | None,
    path_len: int | None,
    path_hash_mode: int | None,
) -> tuple[str, int, int]:
    """Normalize stored contact route fields.

    Handles legacy/bad rows where the packed wire path byte was stored directly
    in the hop-count column (sometimes as a signed byte, e.g. `-125` for `0x83`).
    Returns `(path_hex, hop_count, hash_mode)`.
    """
    normalized_path = path_hex or ""

    try:
        normalized_len = int(path_len) if path_len is not None else -1
    except (TypeError, ValueError):
        normalized_len = -1

    try:
        normalized_mode = int(path_hash_mode) if path_hash_mode is not None else None
    except (TypeError, ValueError):
        normalized_mode = None

    if normalized_len < -1 or normalized_len > 63:
        packed = normalized_len & 0xFF
        if packed == 0xFF:
            return "", -1, -1
        decoded_mode = (packed >> 6) & 0x03
        if decoded_mode != 0x03:
            normalized_len = packed & 0x3F
            normalized_mode = decoded_mode

    if normalized_len == -1:
        return "", -1, -1

    if normalized_mode not in (0, 1, 2):
        normalized_mode = 0

    if normalized_path:
        bytes_per_hop = normalized_mode + 1
        actual_bytes = len(normalized_path) // 2
        expected_bytes = normalized_len * bytes_per_hop
        if actual_bytes > expected_bytes >= 0:
            normalized_path = normalized_path[: expected_bytes * 2]
        elif (
            actual_bytes < expected_bytes
            and bytes_per_hop > 0
            and actual_bytes % bytes_per_hop == 0
        ):
            normalized_len = actual_bytes // bytes_per_hop

    return normalized_path, normalized_len, normalized_mode


def normalize_route_override(
    path_hex: str | None,
    path_len: int | None,
    path_hash_mode: int | None,
) -> tuple[str | None, int | None, int | None]:
    """Normalize optional route-override fields while preserving the unset state."""
    if path_len is None:
        return None, None, None

    normalized_path, normalized_len, normalized_mode = normalize_contact_route(
        path_hex,
        path_len,
        path_hash_mode,
    )
    return normalized_path, normalized_len, normalized_mode


def parse_explicit_hop_route(route_text: str) -> tuple[str, int, int]:
    """Parse a comma-separated explicit hop route into stored contact fields."""
    hops = [hop.strip().lower() for hop in route_text.split(",") if hop.strip()]
    if not hops:
        raise ValueError("Explicit path must include at least one hop")

    hop_chars = len(hops[0])
    if hop_chars not in (2, 4, 6):
        raise ValueError("Each hop must be 1, 2, or 3 bytes of hex")

    for hop in hops:
        if len(hop) != hop_chars:
            raise ValueError("All hops must use the same width")
        try:
            bytes.fromhex(hop)
        except ValueError as exc:
            raise ValueError("Each hop must be valid hex") from exc

    hash_size = hop_chars // 2
    if path_wire_len(len(hops), hash_size) > MAX_PATH_SIZE:
        raise ValueError(f"Explicit path exceeds MAX_PATH_SIZE={MAX_PATH_SIZE} bytes")

    return "".join(hops), len(hops), hash_size - 1


def bucket_path_hash_widths(rows: Iterable) -> dict[str, int | float]:
    """Bucket raw packet rows by hop hash width and return counts + percentages.

    *rows* must be an already-fetched list whose elements have a ``data``
    column containing raw packet bytes.
    """
    single_byte = 0
    double_byte = 0
    triple_byte = 0

    for row in rows:
        envelope = parse_packet_envelope(bytes(row["data"]))
        if envelope is None:
            continue
        if envelope.hash_size == 1:
            single_byte += 1
        elif envelope.hash_size == 2:
            double_byte += 1
        elif envelope.hash_size == 3:
            triple_byte += 1

    total = single_byte + double_byte + triple_byte
    if total == 0:
        return {
            "total_packets": 0,
            "single_byte": 0,
            "double_byte": 0,
            "triple_byte": 0,
            "single_byte_pct": 0.0,
            "double_byte_pct": 0.0,
            "triple_byte_pct": 0.0,
        }

    return {
        "total_packets": total,
        "single_byte": single_byte,
        "double_byte": double_byte,
        "triple_byte": triple_byte,
        "single_byte_pct": (single_byte / total) * 100,
        "double_byte_pct": (double_byte / total) * 100,
        "triple_byte_pct": (triple_byte / total) * 100,
    }


# Payload types with no meaning in the MeshCore protocol. Any packet claiming one
# is corrupt by definition, which makes them a usable gauge for how much RF garbage
# is sitting in a given route-type bucket. See bucket_region_scope().
UNDEFINED_PAYLOAD_TYPES = (0x0C, 0x0D, 0x0E)

_PAYLOAD_TYPE_GROUP_TEXT = 0x05
_FLOOD_ROUTE_TYPES = (0x00, 0x01)  # TRANSPORT_FLOOD, FLOOD


def bucket_region_scope(rows: Iterable) -> dict[str, int | float]:
    """Count flood-routed channel messages carrying a regional transport code.

    *rows* must be an already-fetched list whose elements have a ``data`` column
    containing raw packet bytes.

    Only flood-routed packets are counted. Zero-hop and direct sends can never
    carry transport codes (firmware reaches them through the non-transport
    ``sendZeroHop``/``sendDirect`` overloads), so including them would silently
    dilute the percentage.

    ``false_positive_floor`` exists because corrupt RF captures still land in
    ``raw_packets`` with effectively random header bytes, and a share of them
    claim TRANSPORT_FLOOD. That garbage spreads near-uniformly across payload-type
    buckets, so we measure it directly: count transport-routed packets claiming a
    payload type the protocol does not define, and average per bucket. A
    ``scoped_messages`` count at or below ``false_positive_floor`` is not evidence
    of regional adoption, and callers should present the two together.
    """
    total = 0
    scoped = 0
    undefined_scoped = 0

    for row in rows:
        envelope = parse_packet_envelope(bytes(row["data"]))
        if envelope is None:
            continue

        if (
            envelope.route_type == 0x00
            and envelope.payload_type in UNDEFINED_PAYLOAD_TYPES
            and envelope.transport_codes is not None
        ):
            undefined_scoped += 1
            continue

        if envelope.payload_type != _PAYLOAD_TYPE_GROUP_TEXT:
            continue
        if envelope.route_type not in _FLOOD_ROUTE_TYPES:
            continue

        total += 1
        if envelope.transport_codes is not None:
            scoped += 1

    noise_floor = undefined_scoped / len(UNDEFINED_PAYLOAD_TYPES)
    return {
        "total_messages": total,
        "scoped_messages": scoped,
        "scoped_pct": (scoped / total) * 100 if total else 0.0,
        "false_positive_floor": noise_floor,
    }


def calculate_packet_hash(raw_bytes: bytes) -> str:
    """Calculate packet hash matching MeshCore's Packet::calculatePacketHash().

    Parses the packet structure to extract payload type and payload data,
    then hashes: payload_type(1 byte) [+ path_len(2 bytes LE) for TRACE] + payload_data.
    Returns first 16 hex characters (uppercase).
    """
    if not raw_bytes:
        return "0" * 16

    try:
        envelope = parse_packet_envelope(raw_bytes)
        if envelope is None:
            return "0" * 16

        hash_obj = hashlib.sha256()
        hash_obj.update(bytes([envelope.payload_type]))
        # TRACE hash uses the raw wire byte (not decoded hop count) to match firmware.
        if envelope.payload_type == 9:  # PAYLOAD_TYPE_TRACE
            hash_obj.update(envelope.path_byte.to_bytes(2, byteorder="little"))
        hash_obj.update(envelope.payload)

        return hash_obj.hexdigest()[:16].upper()
    except Exception:
        return "0" * 16
