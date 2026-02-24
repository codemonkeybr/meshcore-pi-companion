"""
Ephemeral keystore for storing sensitive keys in memory.

The private key is stored in memory only and is never persisted to disk.
It's exported from the radio on startup and reconnect, then used for
server-side decryption of direct messages.
"""

import logging
from typing import TYPE_CHECKING

from meshcore import EventType

from app.decoder import derive_public_key

if TYPE_CHECKING:
    from meshcore import MeshCore

logger = logging.getLogger(__name__)

NO_EVENT_RECEIVED_GUIDANCE = (
    "Radio command channel is unresponsive (no_event_received). Possible causes: "
    "incompatible firmware for the meshcore_py command interface; or another "
    "serial/TCP/BLE connectivity error. The app cannot proceed because it cannot "
    "issue commands to the radio."
)

# In-memory storage for the private key and derived public key
_private_key: bytes | None = None
_public_key: bytes | None = None


def set_private_key(key: bytes) -> None:
    """Store the private key in memory and derive the public key.

    Args:
        key: 64-byte Ed25519 private key in MeshCore format
    """
    global _private_key, _public_key
    if len(key) != 64:
        raise ValueError(f"Private key must be 64 bytes, got {len(key)}")
    _private_key = key
    _public_key = derive_public_key(key)
    logger.info("Private key stored in keystore (public key: %s...)", _public_key.hex()[:12])


def get_private_key() -> bytes | None:
    """Get the stored private key.

    Returns:
        The 64-byte private key, or None if not set
    """
    return _private_key


def get_public_key() -> bytes | None:
    """Get the derived public key.

    Returns:
        The 32-byte public key derived from the private key, or None if not set
    """
    return _public_key


def has_private_key() -> bool:
    """Check if a private key is stored.

    Returns:
        True if a private key is available
    """
    return _private_key is not None


async def export_and_store_private_key(mc: "MeshCore") -> bool:
    """Export private key from the radio and store it in the keystore.

    This should be called on startup and after each reconnect.

    Args:
        mc: Connected MeshCore instance

    Returns:
        True if the private key was successfully exported and stored
    """
    logger.info("Exporting private key from radio...")
    try:
        result = await mc.commands.export_private_key()

        if result.type == EventType.PRIVATE_KEY:
            private_key_bytes = result.payload["private_key"]
            set_private_key(private_key_bytes)
            return True
        elif result.type == EventType.DISABLED:
            logger.warning(
                "Private key export disabled on radio firmware. "
                "Server-side DM decryption will not be available. "
                "Enable ENABLE_PRIVATE_KEY_EXPORT=1 in firmware to enable this feature."
            )
            return False
        else:
            reason = result.payload.get("reason") if isinstance(result.payload, dict) else None
            if result.type == EventType.ERROR and reason == "no_event_received":
                logger.error("%s Raw response: %s", NO_EVENT_RECEIVED_GUIDANCE, result.payload)
                raise RuntimeError(NO_EVENT_RECEIVED_GUIDANCE)
            logger.error("Failed to export private key: %s", result.payload)
            return False
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Error exporting private key: %s", e)
        return False
