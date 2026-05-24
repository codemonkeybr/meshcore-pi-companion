"""Fanout module for uploading heard advert packets to map.meshcore.io.

Mirrors the logic of the standalone map.meshcore.dev-uploader project
(historical name; the live service is now hosted at map.meshcore.io):
- Listens on raw RF packets via on_raw
- Filters for ADVERT packets, only processes repeaters (role 2) and rooms (role 3)
- Skips nodes with no valid location (lat/lon None)
- Applies per-pubkey rate-limiting (1-hour window, matching the uploader)
- Signs the upload request with the radio's own Ed25519 private key
- POSTs to the map API (or logs in dry-run mode)

Dry-run mode (default: True) logs the full would-be payload at INFO level
without making any HTTP requests. Disable it only after verifying the log
output looks correct — in particular the radio params (freq/bw/sf/cr) and
the raw hex link.

Config keys
-----------
api_url : str, default ""
    Upload endpoint. Empty string falls back to the public map.meshcore.io API.
dry_run : bool, default True
    When True, log the payload at INFO level instead of sending it.
geofence_enabled : bool, default False
    When True, only upload nodes whose location falls within geofence_radius_km of
    the radio's own configured latitude/longitude (read live from the radio at upload
    time — no lat/lon is stored in this config).  When the radio's lat/lon is not set
    (0, 0) or unavailable, the geofence check is silently skipped so uploads continue
    normally until coordinates are configured.
geofence_radius_km : float, default 0.0
    Radius of the geofence in kilometres. Nodes further than this distance
    from the radio's own position are skipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math

import httpx

from app.decoder import parse_advertisement, parse_packet
from app.fanout.base import FanoutModule
from app.keystore import ed25519_sign_expanded, get_private_key, get_public_key
from app.services.radio_runtime import radio_runtime

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://map.meshcore.io/api/v1/uploader/node"

# Re-upload guard: skip re-uploading a pubkey seen within this window (AU parity)
_REUPLOAD_SECONDS = 3600

# Only upload repeaters (2) and rooms (3). Any other role — including future
# roles not yet defined — is rejected. An allowlist is used rather than a
# blocklist so that new roles cannot accidentally start populating the map.
_ALLOWED_DEVICE_ROLES = {2, 3}


def _get_radio_params() -> dict:
    """Read radio frequency parameters from the connected radio's self_info.

    The Python meshcore library returns radio_freq in MHz (e.g. 910.525) and
    radio_bw in kHz (e.g. 62.5). These are exactly the units the map API
    expects, matching what the JS reference uploader produces after its own
    /1000 division on raw integer values. No further scaling is applied here.
    """
    try:
        mc = radio_runtime.meshcore
        if not mc:
            return {"freq": 0, "cr": 0, "sf": 0, "bw": 0}
        info = mc.self_info
        if not isinstance(info, dict):
            return {"freq": 0, "cr": 0, "sf": 0, "bw": 0}
        freq = info.get("radio_freq", 0) or 0
        bw = info.get("radio_bw", 0) or 0
        sf = info.get("radio_sf", 0) or 0
        cr = info.get("radio_cr", 0) or 0
        return {
            "freq": freq,
            "cr": cr,
            "sf": sf,
            "bw": bw,
        }
    except Exception as exc:
        logger.debug("MapUpload: could not read radio params: %s", exc)
    return {"freq": 0, "cr": 0, "sf": 0, "bw": 0}


_ROLE_NAMES: dict[int, str] = {2: "repeater", 3: "room"}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two lat/lon points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class MapUploadModule(FanoutModule):
    """Uploads heard ADVERT packets to the MeshCore community map."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._client: httpx.AsyncClient | None = None
        # Per-pubkey rate limiting: pubkey_hex -> last_uploaded_advert_timestamp
        self._seen: dict[str, int] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        )
        self._last_error = None
        self._seen.clear()

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._last_error = None

    async def on_raw(self, data: dict) -> None:
        if data.get("payload_type") != "ADVERT":
            return

        raw_hex = data.get("data", "")
        if not raw_hex:
            return

        try:
            raw_bytes = bytes.fromhex(raw_hex)
        except ValueError:
            return

        packet_info = parse_packet(raw_bytes)
        if packet_info is None:
            return

        advert = parse_advertisement(packet_info.payload, raw_packet=raw_bytes)
        if advert is None:
            return

        # Advert Ed25519 signature verification is intentionally skipped.
        # The radio validates packets before passing them to RT.

        # Only process repeaters (2) and rooms (3) — any other role is rejected
        if advert.device_role not in _ALLOWED_DEVICE_ROLES:
            return

        # Skip nodes with no valid location — the decoder already nulls out
        # impossible values, so None means either no location flag or bad coords.
        if advert.lat is None or advert.lon is None:
            logger.debug(
                "MapUpload: skipping %s — no valid location",
                advert.public_key[:12],
            )
            return

        pubkey = advert.public_key.lower()

        # Rate-limit: skip if this pubkey's timestamp hasn't advanced enough
        last_seen = self._seen.get(pubkey)
        if last_seen is not None:
            if last_seen >= advert.timestamp:
                logger.debug(
                    "MapUpload: skipping %s — possible replay (last=%d, advert=%d)",
                    pubkey[:12],
                    last_seen,
                    advert.timestamp,
                )
                return
            if advert.timestamp < last_seen + _REUPLOAD_SECONDS:
                logger.debug(
                    "MapUpload: skipping %s — within 1-hr rate-limit window (delta=%ds)",
                    pubkey[:12],
                    advert.timestamp - last_seen,
                )
                return

        await self._upload(
            pubkey, advert.timestamp, advert.device_role, raw_hex, advert.lat, advert.lon
        )

    async def _upload(
        self,
        pubkey: str,
        advert_timestamp: int,
        device_role: int,
        raw_hex: str,
        lat: float,
        lon: float,
    ) -> None:
        # Geofence check: if enabled, skip nodes outside the configured radius.
        # The reference center is the radio's own lat/lon read live from self_info —
        # no coordinates are stored in the fanout config. If the radio lat/lon is
        # (0, 0) or unavailable the check is skipped transparently so uploads
        # continue normally until the operator sets coordinates in radio settings.
        geofence_dist_km: float | None = None
        if self.config.get("geofence_enabled"):
            try:
                mc = radio_runtime.meshcore
                sinfo = mc.self_info if mc else None
                fence_lat = float((sinfo or {}).get("adv_lat", 0) or 0)
                fence_lon = float((sinfo or {}).get("adv_lon", 0) or 0)
            except Exception as exc:
                logger.debug("MapUpload: could not read radio lat/lon for geofence: %s", exc)
                fence_lat = 0.0
                fence_lon = 0.0

            if fence_lat == 0.0 and fence_lon == 0.0:
                logger.debug(
                    "MapUpload: geofence skipped for %s — radio lat/lon not configured",
                    pubkey[:12],
                )
            else:
                fence_radius_km = float(self.config.get("geofence_radius_km", 0) or 0)
                geofence_dist_km = _haversine_km(fence_lat, fence_lon, lat, lon)
                if geofence_dist_km > fence_radius_km:
                    logger.debug(
                        "MapUpload: skipping %s — outside geofence (%.2f km > %.2f km)",
                        pubkey[:12],
                        geofence_dist_km,
                        fence_radius_km,
                    )
                    return

        private_key = get_private_key()
        public_key = get_public_key()

        if private_key is None or public_key is None:
            logger.warning(
                "MapUpload: private key not available — cannot sign upload for %s. "
                "Ensure radio firmware has ENABLE_PRIVATE_KEY_EXPORT=1.",
                pubkey[:12],
            )
            return

        api_url = str(self.config.get("api_url", "") or _DEFAULT_API_URL).strip()
        dry_run = bool(self.config.get("dry_run", True))
        role_name = _ROLE_NAMES.get(device_role, f"role={device_role}")

        params = _get_radio_params()
        upload_data = {
            "params": params,
            "links": [f"meshcore://{raw_hex}"],
        }

        # Sign: SHA-256 the compact JSON, then Ed25519-sign the hash
        json_str = json.dumps(upload_data, separators=(",", ":"))
        data_hash = hashlib.sha256(json_str.encode()).digest()
        scalar = private_key[:32]
        prefix_bytes = private_key[32:]
        signature = ed25519_sign_expanded(data_hash, scalar, prefix_bytes, public_key)

        request_payload = {
            "data": json_str,
            "signature": signature.hex(),
            "publicKey": public_key.hex(),
        }

        if dry_run:
            geofence_note = (
                f" | geofence: {geofence_dist_km:.2f} km from observer"
                if geofence_dist_km is not None
                else ""
            )
            logger.info(
                "MapUpload [DRY RUN] %s (%s)%s → would POST to %s\n  payload: %s",
                pubkey[:12],
                role_name,
                geofence_note,
                api_url,
                json.dumps(request_payload, separators=(",", ":")),
            )
            # Still update _seen so rate-limiting works during dry-run testing
            self._seen[pubkey] = advert_timestamp
            return

        if not self._client:
            return

        try:
            resp = await self._client.post(
                api_url,
                content=json.dumps(request_payload, separators=(",", ":")),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            self._seen[pubkey] = advert_timestamp
            self._set_last_error(None)
            logger.info(
                "MapUpload: uploaded %s (%s) → HTTP %d",
                pubkey[:12],
                role_name,
                resp.status_code,
            )
        except httpx.HTTPStatusError as exc:
            self._set_last_error(f"HTTP {exc.response.status_code}")
            logger.warning(
                "MapUpload: server returned %d for %s: %s",
                exc.response.status_code,
                pubkey[:12],
                exc.response.text[:200],
            )
        except httpx.RequestError as exc:
            self._set_last_error(str(exc))
            logger.warning("MapUpload: request error for %s: %s", pubkey[:12], exc)

    @property
    def status(self) -> str:
        if self._client is None:
            return "disconnected"
        if self.last_error:
            return "error"
        return "connected"
