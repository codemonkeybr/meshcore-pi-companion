"""Tests for Web Push delivery transport behavior."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from app.push.send import (
    DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
    IPv4HTTPAdapter,
    send_push,
)


@pytest.mark.asyncio
async def test_send_push_prefers_default_dual_stack_session_before_any_ipv4_fallback():
    """Successful sends should use the normal requests transport without forcing IPv4."""
    captured_kwargs: dict = {}

    def fake_webpush(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(status_code=201)

    with patch("app.push.send.webpush", side_effect=fake_webpush):
        status = await send_push(
            subscription_info={"endpoint": "https://push.example.test", "keys": {}},
            payload='{"message":"hello"}',
            vapid_private_key="private-key",
            vapid_claims={"sub": "mailto:test@example.com"},
        )

    assert status == 201
    session = captured_kwargs["requests_session"]
    assert not isinstance(session.adapters["https://"], IPv4HTTPAdapter)
    assert captured_kwargs["timeout"] == (
        DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_send_push_retries_with_ipv4_session_after_connect_timeout():
    """Connect failures should retry through the isolated IPv4-only transport."""
    calls: list[dict] = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.exceptions.ConnectTimeout("ipv6 connect timed out")
        return SimpleNamespace(status_code=201)

    with patch("app.push.send.webpush", side_effect=fake_webpush):
        status = await send_push(
            subscription_info={"endpoint": "https://push.example.test", "keys": {}},
            payload='{"message":"hello"}',
            vapid_private_key="private-key",
            vapid_claims={"sub": "mailto:test@example.com"},
        )

    assert status == 201
    assert len(calls) == 2
    assert not isinstance(calls[0]["requests_session"].adapters["https://"], IPv4HTTPAdapter)
    assert isinstance(calls[1]["requests_session"].adapters["https://"], IPv4HTTPAdapter)
    assert calls[0]["timeout"] == (
        DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )
    assert calls[1]["timeout"] == (
        IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )
