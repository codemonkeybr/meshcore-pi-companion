"""Shared base class for MQTT publisher lifecycle management.

Both ``MqttPublisher`` (private broker) and ``CommunityMqttPublisher``
(community aggregator) inherit from ``BaseMqttPublisher``, which owns
the connection-loop skeleton, reconnect/backoff logic, and publish method.
Subclasses override a small set of hooks to control configuration checks,
client construction, toast messages, and optional wait-loop behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import aiomqtt

from app.models import AppSettings

logger = logging.getLogger(__name__)

_BACKOFF_MIN = 5


def _broadcast_health() -> None:
    """Push updated health (including MQTT status) to all WS clients."""
    from app.radio import radio_manager
    from app.websocket import broadcast_health

    broadcast_health(radio_manager.is_connected, radio_manager.connection_info)


class BaseMqttPublisher(ABC):
    """Base class for MQTT publishers with shared lifecycle management.

    Subclasses implement the abstract hooks to control configuration checks,
    client construction, toast messages, and optional wait-loop behavior.
    """

    _backoff_max: int = 30
    _log_prefix: str = "MQTT"
    _not_configured_timeout: float | None = None  # None = block forever

    def __init__(self) -> None:
        self._client: aiomqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._settings: AppSettings | None = None
        self._settings_version: int = 0
        self._version_event: asyncio.Event = asyncio.Event()
        self.connected: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self, settings: AppSettings) -> None:
        """Start the background connection loop."""
        self._settings = settings
        self._settings_version += 1
        self._version_event.set()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Cancel the background task and disconnect."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        self.connected = False

    async def restart(self, settings: AppSettings) -> None:
        """Called when settings change — stop + start."""
        await self.stop()
        await self.start(settings)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish a JSON payload. Drops silently if not connected."""
        if self._client is None or not self.connected:
            return
        try:
            await self._client.publish(topic, json.dumps(payload))
        except Exception as e:
            logger.warning("%s publish failed on %s: %s", self._log_prefix, topic, e)
            self.connected = False
            # Wake the connection loop so it exits the wait and reconnects
            self._settings_version += 1
            self._version_event.set()

    # ── Abstract hooks ─────────────────────────────────────────────────

    @abstractmethod
    def _is_configured(self) -> bool:
        """Return True when this publisher should attempt to connect."""

    @abstractmethod
    def _build_client_kwargs(self, settings: AppSettings) -> dict[str, Any]:
        """Return the keyword arguments for ``aiomqtt.Client(...)``."""

    @abstractmethod
    def _on_connected(self, settings: AppSettings) -> tuple[str, str]:
        """Return ``(title, detail)`` for the success toast on connect."""

    @abstractmethod
    def _on_error(self) -> tuple[str, str]:
        """Return ``(title, detail)`` for the error toast on connect failure."""

    # ── Optional hooks ─────────────────────────────────────────────────

    def _should_break_wait(self, elapsed: float) -> bool:
        """Return True to break the inner wait (e.g. token expiry)."""
        return False

    async def _pre_connect(self, settings: AppSettings) -> bool:
        """Called before connecting. Return True to proceed, False to retry."""
        return True

    def _on_not_configured(self) -> None:
        """Called each time the loop finds the publisher not configured."""
        return  # no-op by default; subclasses may override

    # ── Connection loop ────────────────────────────────────────────────

    async def _connection_loop(self) -> None:
        """Background loop: connect, wait for version change, reconnect on failure."""
        from app.websocket import broadcast_error, broadcast_success

        backoff = _BACKOFF_MIN

        while True:
            if not self._is_configured():
                self._on_not_configured()
                self.connected = False
                self._client = None
                self._version_event.clear()
                try:
                    if self._not_configured_timeout is None:
                        await self._version_event.wait()
                    else:
                        await asyncio.wait_for(
                            self._version_event.wait(),
                            timeout=self._not_configured_timeout,
                        )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                continue

            settings = self._settings
            assert settings is not None  # guaranteed by _is_configured()
            version_at_connect = self._settings_version

            try:
                if not await self._pre_connect(settings):
                    continue

                client_kwargs = self._build_client_kwargs(settings)
                connect_time = time.monotonic()

                async with aiomqtt.Client(**client_kwargs) as client:
                    self._client = client
                    self.connected = True
                    backoff = _BACKOFF_MIN

                    title, detail = self._on_connected(settings)
                    broadcast_success(title, detail)
                    _broadcast_health()

                    # Wait until cancelled or settings version changes.
                    # The 60s timeout is a housekeeping wake-up; actual connection
                    # liveness is handled by paho-mqtt's keepalive mechanism.
                    while self._settings_version == version_at_connect:
                        self._version_event.clear()
                        try:
                            await asyncio.wait_for(self._version_event.wait(), timeout=60)
                        except asyncio.TimeoutError:
                            elapsed = time.monotonic() - connect_time
                            if self._should_break_wait(elapsed):
                                break
                            continue

                # async with exited — client is now closed
                self._client = None
                self.connected = False
                _broadcast_health()

            except asyncio.CancelledError:
                self.connected = False
                self._client = None
                return

            except Exception as e:
                self.connected = False
                self._client = None

                title, detail = self._on_error()
                broadcast_error(title, detail)
                _broadcast_health()
                logger.warning(
                    "%s connection error: %s (reconnecting in %ds)",
                    self._log_prefix,
                    e,
                    backoff,
                )

                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2, self._backoff_max)
