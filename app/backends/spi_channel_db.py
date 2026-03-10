"""Adapter that presents RemoteTerm's ChannelRepository in the shape pymc_core expects.

pymc_core's ``GroupTextHandler`` calls:
    ``channel_db.get_channels()``
which must return a list of dicts each containing ``"name"`` and ``"secret"``
(the hex-encoded channel key).

Like the contact store, this maintains a synchronous cache refreshed from the
async DB.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SpiChannelDB:
    """Channel database adapter for pymc_core."""

    def __init__(self) -> None:
        self._cache: list[dict[str, str]] = []

    def get_channels(self) -> list[dict[str, str]]:
        """Return channels in the format pymc_core expects."""
        return self._cache

    async def refresh(self) -> None:
        """Reload channels from the database into the in-memory cache."""
        from app.repository import ChannelRepository

        db_channels = await ChannelRepository.get_all()
        new_cache: list[dict[str, str]] = []
        for ch in db_channels:
            new_cache.append({
                "name": ch.name,
                "secret": ch.key,
            })
        self._cache = new_cache
        logger.debug("SpiChannelDB refreshed: %d channels", len(self._cache))
