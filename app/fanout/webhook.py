"""Fanout module for webhook (HTTP POST) delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.fanout.base import FanoutModule

logger = logging.getLogger(__name__)


class WebhookModule(FanoutModule):
    """Delivers message data to an HTTP endpoint via POST (or configurable method)."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._client: httpx.AsyncClient | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._last_error = None

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def on_message(self, data: dict) -> None:
        await self._send(data, event_type="message")

    async def _send(self, data: dict, *, event_type: str) -> None:
        if not self._client:
            return

        url = self.config.get("url", "")
        if not url:
            return

        method = self.config.get("method", "POST").upper()
        extra_headers = self.config.get("headers", {})
        hmac_secret = self.config.get("hmac_secret", "")
        hmac_header = self.config.get("hmac_header", "X-Webhook-Signature")

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event_type,
            **extra_headers,
        }

        body_bytes = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()

        if hmac_secret:
            sig = hmac.new(hmac_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            headers[hmac_header or "X-Webhook-Signature"] = f"sha256={sig}"

        try:
            resp = await self._client.request(method, url, content=body_bytes, headers=headers)
            resp.raise_for_status()
            self._last_error = None
        except httpx.HTTPStatusError as exc:
            self._last_error = f"HTTP {exc.response.status_code}"
            logger.warning(
                "Webhook %s returned %s for %s",
                self.config_id,
                exc.response.status_code,
                url,
            )
        except httpx.RequestError as exc:
            self._last_error = str(exc)
            logger.warning("Webhook %s request error: %s", self.config_id, exc)

    @property
    def status(self) -> str:
        if not self.config.get("url"):
            return "disconnected"
        if self._last_error:
            return "error"
        return "connected"
