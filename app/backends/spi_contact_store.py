"""Adapter that presents RemoteTerm's ContactRepository in the shape pymc_core expects.

pymc_core handlers access contacts via two patterns:
    ``contacts.contacts``       — iterable of objects with ``.public_key`` and ``.name``
    ``contacts.get_by_name(n)`` — lookup by name, returns a contact or ``None``

Because RemoteTerm's repository is async and pymc_core handlers are called from
within the asyncio event loop, we maintain a synchronous in-memory cache that is
periodically refreshed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SpiContact:
    """Minimal contact object compatible with pymc_core handlers."""

    public_key: str  # 64-char hex
    name: str
    out_path: list[int] | None = None


class SpiContactStore:
    """Contact storage adapter for pymc_core.

    Wraps RemoteTerm's ``ContactRepository`` (async DB) into the synchronous
    interface that pymc_core's handlers expect.
    """

    def __init__(self) -> None:
        self._cache: list[SpiContact] = []
        self._by_name: dict[str, SpiContact] = {}

    @property
    def contacts(self) -> list[SpiContact]:
        return self._cache

    def get_by_name(self, name: str) -> SpiContact | None:
        return self._by_name.get(name)

    def get_by_public_key(self, public_key: str) -> SpiContact | None:
        pk = public_key.lower()
        for c in self._cache:
            if c.public_key == pk:
                return c
        return None

    async def refresh(self) -> None:
        """Reload contacts from the database into the in-memory cache."""
        from app.repository import ContactRepository

        db_contacts = await ContactRepository.get_all()
        new_cache: list[SpiContact] = []
        new_by_name: dict[str, SpiContact] = {}
        for c in db_contacts:
            sc = SpiContact(
                public_key=c.public_key.lower(),
                name=c.name or "",
                out_path=_parse_path(c.last_path),
            )
            new_cache.append(sc)
            if sc.name:
                new_by_name[sc.name] = sc
        self._cache = new_cache
        self._by_name = new_by_name
        logger.debug("SpiContactStore refreshed: %d contacts", len(self._cache))

    def add_or_update(self, public_key: str, name: str) -> SpiContact:
        """Add or update a contact in the local cache (does NOT touch DB)."""
        pk = public_key.lower()
        existing = self.get_by_public_key(pk)
        if existing:
            if existing.name:
                self._by_name.pop(existing.name, None)
            existing.name = name
        else:
            existing = SpiContact(public_key=pk, name=name)
            self._cache.append(existing)
        if name:
            self._by_name[name] = existing
        return existing

    def remove(self, public_key: str) -> None:
        pk = public_key.lower()
        self._cache = [c for c in self._cache if c.public_key != pk]
        self._by_name = {n: c for n, c in self._by_name.items() if c.public_key != pk}


def _parse_path(path_str: str | None) -> list[int] | None:
    """Convert a hex path string to a list of integers."""
    if not path_str:
        return None
    try:
        raw = bytes.fromhex(path_str)
        return list(raw)
    except (ValueError, TypeError):
        return None
