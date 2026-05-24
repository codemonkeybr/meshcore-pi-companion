"""VAPID key management for Web Push.

Generates a P-256 key pair on first use and caches it in app_settings
via ``AppSettingsRepository``.  The public key is served to browsers
for ``PushManager.subscribe()``.
"""

import base64
import logging

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

_cached_private_key: str = ""
_cached_public_key: str = ""


async def ensure_vapid_keys() -> tuple[str, str]:
    """Read or generate VAPID keys. Call once at startup after DB connect."""
    global _cached_private_key, _cached_public_key

    private, public = await AppSettingsRepository.get_vapid_keys()
    if private and public:
        _cached_private_key = private
        _cached_public_key = public
        logger.info("VAPID keys loaded from database")
        return _cached_private_key, _cached_public_key

    # Generate new key pair
    vapid = Vapid()
    vapid.generate_keys()

    # Private key as base64url-encoded raw 32-byte EC scalar — the format
    # that pywebpush passes to ``Vapid.from_string()``.
    raw_priv = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")  # type: ignore[union-attr]
    _cached_private_key = base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode("ascii")

    # Public key as uncompressed P-256 point, base64url-encoded (no padding)
    # for the browser Push API's applicationServerKey
    raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)  # type: ignore[union-attr]
    _cached_public_key = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")

    await AppSettingsRepository.set_vapid_keys(_cached_private_key, _cached_public_key)
    logger.info("Generated and stored new VAPID key pair")

    return _cached_private_key, _cached_public_key


def get_vapid_public_key() -> str:
    """Return the cached VAPID public key (base64url). Must call ensure_vapid_keys() first."""
    return _cached_public_key


def get_vapid_private_key() -> str:
    """Return the cached VAPID private key (base64url). Must call ensure_vapid_keys() first."""
    return _cached_private_key
