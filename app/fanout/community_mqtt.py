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
import importlib.metadata
import json
import logging
import ssl
import time
from datetime import datetime
from typing import Any, Protocol

import aiomqtt
import nacl.bindings

from app.fanout.mqtt_base import BaseMqttPublisher
from app.path_utils import parse_packet_envelope, split_path_hex

logger = logging.getLogger(__name__)

_DEFAULT_BROKER = "mqtt-us-v1.letsmesh.net"
_DEFAULT_PORT = 443  # Community protocol uses WSS on port 443 by default
_CLIENT_ID = "RemoteTerm (github.com/jkingsman/Remote-Terminal-for-MeshCore)"

# Proactive JWT renewal: reconnect 1 hour before the 24h token expires
_TOKEN_LIFETIME = 86400  # 24 hours (must match _generate_jwt_token exp)
_TOKEN_RENEWAL_THRESHOLD = _TOKEN_LIFETIME - 3600  # 23 hours

# Periodic status republish interval (matches meshcore-packet-capture reference)
_STATS_REFRESH_INTERVAL = 300  # 5 minutes
_STATS_MIN_CACHE_SECS = 60  # Don't re-fetch stats within 60s

# Ed25519 group order
_L = 2**252 + 27742317777372353535851937790883648493

# Route type mapping: bottom 2 bits of first byte
_ROUTE_MAP = {0: "F", 1: "F", 2: "D", 3: "T"}


class CommunityMqttSettings(Protocol):
    """Attributes expected on the settings object for the community MQTT publisher."""

    community_mqtt_enabled: bool
    community_mqtt_broker_host: str
    community_mqtt_broker_port: int
    community_mqtt_transport: str
    community_mqtt_use_tls: bool
    community_mqtt_tls_verify: bool
    community_mqtt_auth_mode: str
    community_mqtt_username: str
    community_mqtt_password: str
    community_mqtt_iata: str
    community_mqtt_email: str
    community_mqtt_token_audience: str


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
        envelope = parse_packet_envelope(raw_bytes)
        if envelope is None:
            return "0" * 16

        # Hash: payload_type(1 byte) [+ path_byte as uint16_t LE for TRACE] + payload_data
        # IMPORTANT: TRACE hash uses the raw wire byte (not decoded hop count) to match firmware.
        hash_obj = hashlib.sha256()
        hash_obj.update(bytes([envelope.payload_type]))
        if envelope.payload_type == 9:  # PAYLOAD_TYPE_TRACE
            hash_obj.update(envelope.path_byte.to_bytes(2, byteorder="little"))
        hash_obj.update(envelope.payload)

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
        envelope = parse_packet_envelope(raw_bytes)
        if envelope is None or envelope.payload_version != 0:
            return route, packet_type, payload_len, path_values, payload_type

        payload_type = envelope.payload_type
        route = _ROUTE_MAP.get(envelope.route_type, "U")
        packet_type = str(payload_type)
        payload_len = str(len(envelope.payload))
        path_values = split_path_hex(envelope.path.hex(), envelope.hop_count)

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


def _build_status_topic(settings: CommunityMqttSettings, pubkey_hex: str) -> str:
    """Build the ``meshcore/{IATA}/{PUBKEY}/status`` topic string."""
    iata = settings.community_mqtt_iata.upper().strip()
    return f"meshcore/{iata}/{pubkey_hex}/status"


def _build_radio_info() -> str:
    """Format the radio parameters string from self_info.

    Matches the reference format: ``"freq,bw,sf,cr"`` (comma-separated raw
    values).  Falls back to ``"0,0,0,0"`` when unavailable.
    """
    from app.services.radio_runtime import radio_runtime as radio_manager

    try:
        if radio_manager.backend and radio_manager.backend.self_info:
            info = radio_manager.backend.self_info
            freq = info.get("radio_freq", 0)
            bw = info.get("radio_bw", 0)
            sf = info.get("radio_sf", 0)
            cr = info.get("radio_cr", 0)
            return f"{freq},{bw},{sf},{cr}"
    except Exception:
        pass
    return "0,0,0,0"


def _get_client_version() -> str:
    """Return a client version string like ``'RemoteTerm 2.4.0'``."""
    try:
        version = importlib.metadata.version("remoteterm-meshcore")
        return f"RemoteTerm {version}"
    except Exception:
        return "RemoteTerm unknown"


class CommunityMqttPublisher(BaseMqttPublisher):
    """Manages the community MQTT connection and publishes raw packets."""

    _backoff_max = 60
    _log_prefix = "Community MQTT"
    _not_configured_timeout: float | None = 30

    def __init__(self) -> None:
        super().__init__()
        self._key_unavailable_warned: bool = False
        self._cached_device_info: dict[str, str] | None = None
        self._cached_stats: dict[str, Any] | None = None
        self._stats_supported: bool | None = None
        self._last_stats_fetch: float = 0.0
        self._last_status_publish: float = 0.0

    async def start(self, settings: object) -> None:
        self._key_unavailable_warned = False
        self._cached_device_info = None
        self._cached_stats = None
        self._stats_supported = None
        self._last_stats_fetch = 0.0
        self._last_status_publish = 0.0
        await super().start(settings)

    def _on_not_configured(self) -> None:
        from app.keystore import get_public_key, has_private_key
        from app.websocket import broadcast_error

        s: CommunityMqttSettings | None = self._settings
        auth_mode = getattr(s, "community_mqtt_auth_mode", "token") if s else "token"
        if (
            s
            and auth_mode == "token"
            and get_public_key() is not None
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
        from app.keystore import get_public_key, has_private_key

        s: CommunityMqttSettings | None = self._settings
        if not s or not s.community_mqtt_enabled:
            return False
        if get_public_key() is None:
            return False
        auth_mode = getattr(s, "community_mqtt_auth_mode", "token")
        if auth_mode == "token":
            return has_private_key()
        return True

    def _build_client_kwargs(self, settings: object) -> dict[str, Any]:
        s: CommunityMqttSettings = settings  # type: ignore[assignment]
        from app.keystore import get_private_key, get_public_key
        from app.services.radio_runtime import radio_runtime as radio_manager

        private_key = get_private_key()
        public_key = get_public_key()
        assert public_key is not None  # guaranteed by _pre_connect

        pubkey_hex = public_key.hex().upper()
        broker_host = s.community_mqtt_broker_host or _DEFAULT_BROKER
        broker_port = s.community_mqtt_broker_port or _DEFAULT_PORT
        transport = s.community_mqtt_transport or "websockets"
        use_tls = bool(s.community_mqtt_use_tls)
        tls_verify = bool(s.community_mqtt_tls_verify)
        auth_mode = s.community_mqtt_auth_mode or "token"
        secure_connection = use_tls and tls_verify

        tls_context: ssl.SSLContext | None = None
        if use_tls:
            tls_context = ssl.create_default_context()
            if not tls_verify:
                tls_context.check_hostname = False
                tls_context.verify_mode = ssl.CERT_NONE

        device_name = ""
        if radio_manager.backend and radio_manager.backend.self_info:
            device_name = radio_manager.backend.self_info.get("name", "")

        status_topic = _build_status_topic(s, pubkey_hex)
        offline_payload = json.dumps(
            {
                "status": "offline",
                "timestamp": datetime.now().isoformat(),
                "origin": device_name or "MeshCore Device",
                "origin_id": pubkey_hex,
            }
        )

        kwargs: dict[str, Any] = {
            "hostname": broker_host,
            "port": broker_port,
            "transport": transport,
            "tls_context": tls_context,
            "will": aiomqtt.Will(status_topic, offline_payload, retain=True),
        }
        if auth_mode == "token":
            assert private_key is not None
            token_audience = (s.community_mqtt_token_audience or "").strip() or broker_host
            jwt_token = _generate_jwt_token(
                private_key,
                public_key,
                audience=token_audience,
                email=(s.community_mqtt_email or "") if secure_connection else "",
            )
            kwargs["username"] = f"v1_{pubkey_hex}"
            kwargs["password"] = jwt_token
        elif auth_mode == "password":
            kwargs["username"] = s.community_mqtt_username or None
            kwargs["password"] = s.community_mqtt_password or None
        if transport == "websockets":
            kwargs["websocket_path"] = "/"
        return kwargs

    def _on_connected(self, settings: object) -> tuple[str, str]:
        s: CommunityMqttSettings = settings  # type: ignore[assignment]
        broker_host = s.community_mqtt_broker_host or _DEFAULT_BROKER
        broker_port = s.community_mqtt_broker_port or _DEFAULT_PORT
        return ("Community MQTT connected", f"{broker_host}:{broker_port}")

    async def _fetch_device_info(self) -> dict[str, str]:
        """Fetch firmware model/version from the radio (cached for the connection)."""
        if self._cached_device_info is not None:
            return self._cached_device_info

        from app.radio import RadioDisconnectedError, RadioOperationBusyError
        from app.services.radio_runtime import radio_runtime as radio_manager

        fallback = {"model": "unknown", "firmware_version": "unknown"}
        try:
            async with radio_manager.radio_operation(
                "community_stats_device_info", blocking=False
            ) as mc:
                event = await mc.send_device_query()
                from meshcore.events import EventType

                if event.type == EventType.DEVICE_INFO:
                    fw_ver = event.payload.get("fw ver", 0)
                    if fw_ver >= 3:
                        model = event.payload.get("model", "unknown") or "unknown"
                        ver = event.payload.get("ver", "unknown") or "unknown"
                        fw_build = event.payload.get("fw_build", "") or ""
                        fw_str = f"v{ver} (Build: {fw_build})" if fw_build else f"v{ver}"
                        self._cached_device_info = {
                            "model": model,
                            "firmware_version": fw_str,
                        }
                    else:
                        # Old firmware — cache what we can
                        self._cached_device_info = {
                            "model": "unknown",
                            "firmware_version": f"v{fw_ver}" if fw_ver else "unknown",
                        }
                    return self._cached_device_info
        except (RadioOperationBusyError, RadioDisconnectedError):
            pass
        except Exception as e:
            logger.debug("Community MQTT: device info fetch failed: %s", e)

        # Don't cache transient failures — allow retry on next status publish
        return fallback

    async def _fetch_stats(self) -> dict[str, Any] | None:
        """Fetch core + radio stats from the radio (best-effort, cached)."""
        if self._stats_supported is False:
            return self._cached_stats

        now = time.monotonic()
        if (
            now - self._last_stats_fetch
        ) < _STATS_MIN_CACHE_SECS and self._cached_stats is not None:
            return self._cached_stats

        from app.radio import RadioDisconnectedError, RadioOperationBusyError
        from app.services.radio_runtime import radio_runtime as radio_manager

        try:
            async with radio_manager.radio_operation("community_stats_fetch", blocking=False) as mc:
                from meshcore.events import EventType

                result: dict[str, Any] = {}

                core_event = await mc.get_stats_core()
                if core_event.type == EventType.ERROR:
                    logger.info("Community MQTT: firmware does not support stats commands")
                    self._stats_supported = False
                    return self._cached_stats
                if core_event.type == EventType.STATS_CORE:
                    result.update(core_event.payload)

                radio_event = await mc.get_stats_radio()
                if radio_event.type == EventType.ERROR:
                    logger.info("Community MQTT: firmware does not support stats commands")
                    self._stats_supported = False
                    return self._cached_stats
                if radio_event.type == EventType.STATS_RADIO:
                    result.update(radio_event.payload)

                if result:
                    self._cached_stats = result
                    self._last_stats_fetch = now
                    return self._cached_stats

        except (RadioOperationBusyError, RadioDisconnectedError):
            pass
        except Exception as e:
            logger.debug("Community MQTT: stats fetch failed: %s", e)

        return self._cached_stats

    async def _publish_status(
        self, settings: CommunityMqttSettings, *, refresh_stats: bool = True
    ) -> None:
        """Build and publish the enriched retained status message."""
        from app.keystore import get_public_key
        from app.services.radio_runtime import radio_runtime as radio_manager

        public_key = get_public_key()
        if public_key is None:
            return

        pubkey_hex = public_key.hex().upper()

        device_name = ""
        if radio_manager.backend and radio_manager.backend.self_info:
            device_name = radio_manager.backend.self_info.get("name", "")

        device_info = await self._fetch_device_info()
        stats = await self._fetch_stats() if refresh_stats else self._cached_stats

        status_topic = _build_status_topic(settings, pubkey_hex)
        payload: dict[str, Any] = {
            "status": "online",
            "timestamp": datetime.now().isoformat(),
            "origin": device_name or "MeshCore Device",
            "origin_id": pubkey_hex,
            "model": device_info.get("model", "unknown"),
            "firmware_version": device_info.get("firmware_version", "unknown"),
            "radio": _build_radio_info(),
            "client_version": _get_client_version(),
        }
        if stats:
            payload["stats"] = stats

        await self.publish(status_topic, payload, retain=True)
        self._last_status_publish = time.monotonic()

    async def _on_connected_async(self, settings: object) -> None:
        """Publish a retained online status message after connecting."""
        await self._publish_status(settings)  # type: ignore[arg-type]

    async def _on_periodic_wake(self, elapsed: float) -> None:
        if not self._settings:
            return
        now = time.monotonic()
        if (now - self._last_status_publish) >= _STATS_REFRESH_INTERVAL:
            await self._publish_status(self._settings, refresh_stats=True)

    def _on_error(self) -> tuple[str, str]:
        return (
            "Community MQTT connection failure",
            "Check your internet connection or try again later.",
        )

    def _should_break_wait(self, elapsed: float) -> bool:
        if not self.connected:
            logger.info("Community MQTT publish failure detected, reconnecting")
            return True
        s: CommunityMqttSettings | None = self._settings
        auth_mode = getattr(s, "community_mqtt_auth_mode", "token") if s else "token"
        if auth_mode == "token" and elapsed >= _TOKEN_RENEWAL_THRESHOLD:
            logger.info("Community MQTT JWT nearing expiry, reconnecting")
            return True
        return False

    async def _pre_connect(self, settings: object) -> bool:
        from app.keystore import get_private_key, get_public_key

        s: CommunityMqttSettings = settings  # type: ignore[assignment]
        auth_mode = s.community_mqtt_auth_mode or "token"
        private_key = get_private_key()
        public_key = get_public_key()
        if public_key is None or (auth_mode == "token" and private_key is None):
            # Keys not available yet, wait for settings change or key export
            self.connected = False
            self._version_event.clear()
            try:
                await asyncio.wait_for(self._version_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            return False
        return True
