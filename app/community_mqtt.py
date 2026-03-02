"""Community MQTT publisher for sharing raw packets with the MeshCore community.

Publishes raw packet data to mqtt-us-v1.letsmesh.net using the protocol
defined by meshcore-packet-capture (https://github.com/agessaman/meshcore-packet-capture).

Authentication uses Ed25519 JWT tokens signed with the radio's private key.
This module is independent from the private MqttPublisher in app/mqtt.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import ssl
import time
from datetime import datetime
from typing import Any

import nacl.bindings

from app.models import AppSettings
from app.mqtt_base import BaseMqttPublisher

logger = logging.getLogger(__name__)

_DEFAULT_BROKER = "mqtt-us-v1.letsmesh.net"
_DEFAULT_PORT = 443  # Community protocol uses WSS on port 443 by default
_CLIENT_ID = "RemoteTerm (github.com/jkingsman/Remote-Terminal-for-MeshCore)"

# Proactive JWT renewal: reconnect 1 hour before the 24h token expires
_TOKEN_LIFETIME = 86400  # 24 hours (must match _generate_jwt_token exp)
_TOKEN_RENEWAL_THRESHOLD = _TOKEN_LIFETIME - 3600  # 23 hours

# Ed25519 group order
_L = 2**252 + 27742317777372353535851937790883648493
_IATA_RE = re.compile(r"^[A-Z]{3}$")

# Route type mapping: bottom 2 bits of first byte
_ROUTE_MAP = {0: "F", 1: "F", 2: "D", 3: "T"}


def _parse_broker_address(broker_str: str) -> tuple[str, int]:
    """Parse 'host' or 'host:port' into (host, port). Defaults to _DEFAULT_PORT."""
    if ":" in broker_str:
        host, port_str = broker_str.rsplit(":", 1)
        try:
            return host, int(port_str)
        except ValueError:
            pass
    return broker_str, _DEFAULT_PORT


def _base64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ed25519_sign_expanded(
    message: bytes, scalar: bytes, prefix: bytes, public_key: bytes
) -> bytes:
    """Sign a message using MeshCore's expanded Ed25519 key format.

    MeshCore stores 64-byte "orlp" format keys: scalar(32) || prefix(32).
    Standard Ed25519 libraries expect seed format and would re-SHA-512 the key.
    This performs the signing manually using the already-expanded key material.

    Port of meshcore-packet-capture's ed25519_sign_with_expanded_key().
    """
    # r = SHA-512(prefix || message) mod L
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    # R = r * B (base point multiplication)
    R = nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(r.to_bytes(32, "little"))
    # k = SHA-512(R || public_key || message) mod L
    k = int.from_bytes(hashlib.sha512(R + public_key + message).digest(), "little") % _L
    # s = (r + k * scalar) mod L
    s = (r + k * int.from_bytes(scalar, "little")) % _L
    return R + s.to_bytes(32, "little")


def _generate_jwt_token(
    private_key: bytes,
    public_key: bytes,
    *,
    audience: str = _DEFAULT_BROKER,
    email: str = "",
) -> str:
    """Generate a JWT token for community MQTT authentication.

    Creates a token with Ed25519 signature using MeshCore's expanded key format.
    Token format: header_b64.payload_b64.signature_hex

    Optional ``email`` embeds a node-claiming identity so the community
    aggregator can associate this radio with an owner.
    """
    header = {"alg": "Ed25519", "typ": "JWT"}
    now = int(time.time())
    pubkey_hex = public_key.hex().upper()
    payload: dict[str, object] = {
        "publicKey": pubkey_hex,
        "iat": now,
        "exp": now + _TOKEN_LIFETIME,
        "aud": audience,
        "owner": pubkey_hex,
        "client": _CLIENT_ID,
    }
    if email:
        payload["email"] = email

    header_b64 = _base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}".encode()

    scalar = private_key[:32]
    prefix = private_key[32:]
    signature = _ed25519_sign_expanded(signing_input, scalar, prefix, public_key)

    return f"{header_b64}.{payload_b64}.{signature.hex()}"


def _calculate_packet_hash(raw_bytes: bytes) -> str:
    """Calculate packet hash matching MeshCore's Packet::calculatePacketHash().

    Parses the packet structure to extract payload type and payload data,
    then hashes: payload_type(1 byte) [+ path_len(2 bytes LE) for TRACE] + payload_data.
    Returns first 16 hex characters (uppercase).
    """
    if not raw_bytes:
        return "0" * 16

    try:
        header = raw_bytes[0]
        payload_type = (header >> 2) & 0x0F
        route_type = header & 0x03

        # Transport codes present for TRANSPORT_FLOOD (0) and TRANSPORT_DIRECT (3)
        has_transport = route_type in (0x00, 0x03)

        offset = 1  # Past header
        if has_transport:
            offset += 4  # Skip 4 bytes of transport codes

        # Read path_len (1 byte on wire). Invalid/truncated packets map to zero hash.
        if offset >= len(raw_bytes):
            return "0" * 16
        path_len = raw_bytes[offset]
        offset += 1

        # Skip past path to get to payload. Invalid/truncated packets map to zero hash.
        if len(raw_bytes) < offset + path_len:
            return "0" * 16
        payload_start = offset + path_len
        payload_data = raw_bytes[payload_start:]

        # Hash: payload_type(1 byte) [+ path_len as uint16_t LE for TRACE] + payload_data
        hash_obj = hashlib.sha256()
        hash_obj.update(bytes([payload_type]))
        if payload_type == 9:  # PAYLOAD_TYPE_TRACE
            hash_obj.update(path_len.to_bytes(2, byteorder="little"))
        hash_obj.update(payload_data)

        return hash_obj.hexdigest()[:16].upper()
    except Exception:
        return "0" * 16


def _decode_packet_fields(raw_bytes: bytes) -> tuple[str, str, str, list[str], int | None]:
    """Decode packet fields used by the community uploader payload format.

    Returns:
        (route_letter, packet_type_str, payload_len_str, path_values, payload_type_int)
    """
    # Reference defaults when decode fails
    route = "U"
    packet_type = "0"
    payload_len = "0"
    path_values: list[str] = []
    payload_type: int | None = None

    try:
        if len(raw_bytes) < 2:
            return route, packet_type, payload_len, path_values, payload_type

        header = raw_bytes[0]
        payload_version = (header >> 6) & 0x03
        if payload_version != 0:
            return route, packet_type, payload_len, path_values, payload_type

        route_type = header & 0x03
        has_transport = route_type in (0x00, 0x03)

        offset = 1
        if has_transport:
            offset += 4

        if len(raw_bytes) <= offset:
            return route, packet_type, payload_len, path_values, payload_type

        path_len = raw_bytes[offset]
        offset += 1

        if len(raw_bytes) < offset + path_len:
            return route, packet_type, payload_len, path_values, payload_type

        path_bytes = raw_bytes[offset : offset + path_len]
        offset += path_len

        payload_type = (header >> 2) & 0x0F
        route = _ROUTE_MAP.get(route_type, "U")
        packet_type = str(payload_type)
        payload_len = str(max(0, len(raw_bytes) - offset))
        path_values = [f"{b:02x}" for b in path_bytes]

        return route, packet_type, payload_len, path_values, payload_type
    except Exception:
        return route, packet_type, payload_len, path_values, payload_type


def _format_raw_packet(data: dict[str, Any], device_name: str, public_key_hex: str) -> dict:
    """Convert a RawPacketBroadcast dict to meshcore-packet-capture format."""
    raw_hex = data.get("data", "")
    raw_bytes = bytes.fromhex(raw_hex) if raw_hex else b""

    route, packet_type, payload_len, path_values, _payload_type = _decode_packet_fields(raw_bytes)

    # Reference format uses local "now" timestamp and derived time/date fields.
    current_time = datetime.now()
    ts_str = current_time.isoformat()

    # SNR/RSSI are always strings in reference output.
    snr_val = data.get("snr")
    rssi_val = data.get("rssi")
    snr = str(snr_val) if snr_val is not None else "Unknown"
    rssi = str(rssi_val) if rssi_val is not None else "Unknown"

    packet_hash = _calculate_packet_hash(raw_bytes)

    packet = {
        "origin": device_name or "MeshCore Device",
        "origin_id": public_key_hex.upper(),
        "timestamp": ts_str,
        "type": "PACKET",
        "direction": "rx",
        "time": current_time.strftime("%H:%M:%S"),
        "date": current_time.strftime("%d/%m/%Y"),
        "len": str(len(raw_bytes)),
        "packet_type": packet_type,
        "route": route,
        "payload_len": payload_len,
        "raw": raw_hex.upper(),
        "SNR": snr,
        "RSSI": rssi,
        "hash": packet_hash,
    }

    if route == "D":
        packet["path"] = ",".join(path_values)

    return packet


class CommunityMqttPublisher(BaseMqttPublisher):
    """Manages the community MQTT connection and publishes raw packets."""

    _backoff_max = 60
    _log_prefix = "Community MQTT"
    _not_configured_timeout: float | None = 30

    def __init__(self) -> None:
        super().__init__()
        self._key_unavailable_warned: bool = False

    async def start(self, settings: AppSettings) -> None:
        self._key_unavailable_warned = False
        await super().start(settings)

    def _on_not_configured(self) -> None:
        from app.keystore import has_private_key
        from app.websocket import broadcast_error

        if (
            self._settings
            and self._settings.community_mqtt_enabled
            and not has_private_key()
            and not self._key_unavailable_warned
        ):
            broadcast_error(
                "Community MQTT unavailable",
                "Radio firmware does not support private key export.",
            )
            self._key_unavailable_warned = True

    def _is_configured(self) -> bool:
        """Check if community MQTT is enabled and keys are available."""
        from app.keystore import has_private_key

        return bool(self._settings and self._settings.community_mqtt_enabled and has_private_key())

    def _build_client_kwargs(self, settings: AppSettings) -> dict[str, Any]:
        from app.keystore import get_private_key, get_public_key

        private_key = get_private_key()
        public_key = get_public_key()
        assert private_key is not None and public_key is not None  # guaranteed by _pre_connect

        pubkey_hex = public_key.hex().upper()
        broker_raw = settings.community_mqtt_broker or _DEFAULT_BROKER
        broker_host, broker_port = _parse_broker_address(broker_raw)
        jwt_token = _generate_jwt_token(
            private_key,
            public_key,
            audience=broker_host,
            email=settings.community_mqtt_email or "",
        )

        tls_context = ssl.create_default_context()

        return {
            "hostname": broker_host,
            "port": broker_port,
            "transport": "websockets",
            "tls_context": tls_context,
            "websocket_path": "/",
            "username": f"v1_{pubkey_hex}",
            "password": jwt_token,
        }

    def _on_connected(self, settings: AppSettings) -> tuple[str, str]:
        broker_raw = settings.community_mqtt_broker or _DEFAULT_BROKER
        broker_host, broker_port = _parse_broker_address(broker_raw)
        return ("Community MQTT connected", f"{broker_host}:{broker_port}")

    def _on_error(self) -> tuple[str, str]:
        return (
            "Community MQTT connection failure",
            "Check your internet connection or try again later.",
        )

    def _should_break_wait(self, elapsed: float) -> bool:
        if not self.connected:
            logger.info("Community MQTT publish failure detected, reconnecting")
            return True
        if elapsed >= _TOKEN_RENEWAL_THRESHOLD:
            logger.info("Community MQTT JWT nearing expiry, reconnecting")
            return True
        return False

    async def _pre_connect(self, settings: AppSettings) -> bool:
        from app.keystore import get_private_key, get_public_key

        private_key = get_private_key()
        public_key = get_public_key()
        if private_key is None or public_key is None:
            # Keys not available yet, wait for settings change or key export
            self.connected = False
            self._version_event.clear()
            try:
                await asyncio.wait_for(self._version_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            return False
        return True


# Module-level singleton
community_publisher = CommunityMqttPublisher()


def community_mqtt_broadcast(event_type: str, data: dict[str, Any]) -> None:
    """Fire-and-forget community MQTT publish for raw packets only."""
    if event_type != "raw_packet":
        return
    if not community_publisher.connected or community_publisher._settings is None:
        return
    asyncio.create_task(_community_maybe_publish(data))


async def _community_maybe_publish(data: dict[str, Any]) -> None:
    """Format and publish a raw packet to the community broker."""
    settings = community_publisher._settings
    if settings is None or not settings.community_mqtt_enabled:
        return

    try:
        from app.keystore import get_public_key
        from app.radio import radio_manager

        public_key = get_public_key()
        if public_key is None:
            return

        pubkey_hex = public_key.hex().upper()

        # Get device name from radio
        device_name = ""
        if radio_manager.meshcore and radio_manager.meshcore.self_info:
            device_name = radio_manager.meshcore.self_info.get("name", "")

        packet = _format_raw_packet(data, device_name, pubkey_hex)
        iata = settings.community_mqtt_iata.upper().strip()
        if not _IATA_RE.fullmatch(iata):
            logger.debug("Community MQTT: skipping publish — no valid IATA code configured")
            return
        topic = f"meshcore/{iata}/{pubkey_hex}/packets"

        await community_publisher.publish(topic, packet)

    except Exception as e:
        logger.warning("Community MQTT broadcast error: %s", e)
