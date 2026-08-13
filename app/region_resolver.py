"""Resolve a packet's regional flood-scope (transport code) back to a region name.

MeshCore's TransportFlood/TransportDirect packets carry a 16-bit "transport code"
derived from the region name *and the packet payload* — it is a keyed MAC, not a
stable per-region identifier:

    key  = SHA256("#" + region_name)[:16]            # firmware TransportKey
    code = HMAC-SHA256(key, payload_type || payload)[:2]  # little-endian uint16

(see ``references/MeshCore/src/helpers/TransportKeyStore.cpp``). Codes ``0x0000``
and ``0xFFFF`` are reserved, so the firmware nudges them to ``0x0001`` / ``0xFFFE``.

Because the code depends on the payload, there is no reverse lookup table: to
name a packet's region we recompute the code for each known region name and check
for a match. The candidate region names come from the server-side
``app_settings.known_regions`` list.
"""

import hashlib
import hmac

from app.region_scope import normalize_region_scope

# SHA256("#name")[:16] is deterministic and cheap, but region lists are tiny and
# packets are frequent, so cache the derived 16-byte keys by normalized name.
_key_cache: dict[str, bytes] = {}


def _region_key(region_name: str) -> bytes | None:
    """Return the 16-byte TransportKey for a region name, or None if blank.

    Region names are hashed in their hashtag form (``#name``), matching the
    firmware (``getAutoKeyFor(id, "#" + name, ...)``).
    """
    normalized = normalize_region_scope(region_name)
    if not normalized:
        return None
    key = _key_cache.get(normalized)
    if key is None:
        key = hashlib.sha256(normalized.encode("utf-8")).digest()[:16]
        _key_cache[normalized] = key
    return key


def compute_transport_code(region_name: str, payload_type: int, payload: bytes) -> int | None:
    """Compute the transport code a region would produce for this payload.

    Returns the little-endian uint16 code (with the firmware's reserved-value
    adjustment applied), or None if the region name is blank.
    """
    key = _region_key(region_name)
    if key is None:
        return None
    digest = hmac.new(key, bytes([payload_type & 0xFF]) + payload, hashlib.sha256).digest()
    code = int.from_bytes(digest[:2], "little")
    if code == 0:
        code = 1
    elif code == 0xFFFF:
        code = 0xFFFE
    return code


def resolve_region(
    payload_type: int,
    payload: bytes,
    transport_code: int,
    region_names: list[str],
) -> str | None:
    """Return the first region name whose code matches ``transport_code``, else None.

    The returned name is the user-facing form as stored in the candidate list
    (no ``#`` prefix is added or stripped here).
    """
    for name in region_names:
        if not name:
            continue
        if compute_transport_code(name, payload_type, payload) == transport_code:
            return name
    return None
