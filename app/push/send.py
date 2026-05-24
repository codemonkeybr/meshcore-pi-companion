"""Thin wrapper around pywebpush for sending push notifications.

Isolates the pywebpush dependency and runs the synchronous send in
a thread executor to avoid blocking the event loop.
"""

import asyncio
import logging
import socket
from typing import Any, cast

import requests
import urllib3.connection
import urllib3.connectionpool
from pywebpush import webpush
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout as RequestsConnectTimeout
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = object()
DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS = 3
IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_PUSH_READ_TIMEOUT_SECONDS = 10


def _create_ipv4_connection(
    address: tuple[str, int],
    timeout: float | None | object = DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    socket_options=None,
) -> socket.socket:
    """Create a socket connection using IPv4 only."""
    host, port = address
    if host.startswith("["):
        host = host.strip("[]")

    err: OSError | None = None
    for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        af, socktype, proto, _, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if socket_options:
                for opt in socket_options:
                    sock.setsockopt(*opt)
            if timeout is not DEFAULT_TIMEOUT:
                sock.settimeout(cast(float | None, timeout))
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()

    if err is not None:
        raise err
    raise OSError("getaddrinfo returns an empty list")


class IPv4HTTPConnection(urllib3.connection.HTTPConnection):
    """urllib3 HTTP connection that resolves and connects via IPv4 only."""

    def _new_conn(self) -> socket.socket:
        try:
            return _create_ipv4_connection(
                (self._dns_host, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except TimeoutError as exc:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out. (connect timeout={self.timeout})",
            ) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a new connection: {exc}") from exc


class IPv4HTTPSConnection(urllib3.connection.HTTPSConnection):
    """urllib3 HTTPS connection that resolves and connects via IPv4 only."""

    def _new_conn(self) -> socket.socket:
        try:
            return _create_ipv4_connection(
                (self._dns_host, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except TimeoutError as exc:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out. (connect timeout={self.timeout})",
            ) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a new connection: {exc}") from exc


class IPv4HTTPConnectionPool(urllib3.connectionpool.HTTPConnectionPool):
    ConnectionCls = cast(Any, IPv4HTTPConnection)


class IPv4HTTPSConnectionPool(urllib3.connectionpool.HTTPSConnectionPool):
    ConnectionCls = cast(Any, IPv4HTTPSConnection)


def _configure_pool_manager_for_ipv4(manager: Any) -> None:
    manager.pool_classes_by_scheme = manager.pool_classes_by_scheme.copy()
    manager.pool_classes_by_scheme["http"] = IPv4HTTPConnectionPool
    manager.pool_classes_by_scheme["https"] = IPv4HTTPSConnectionPool


class IPv4HTTPAdapter(HTTPAdapter):
    """requests adapter that uses IPv4-only urllib3 connection pools."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        _configure_pool_manager_for_ipv4(self.poolmanager)

    def proxy_manager_for(self, *args, **kwargs):
        manager = super().proxy_manager_for(*args, **kwargs)
        _configure_pool_manager_for_ipv4(manager)
        return manager


def _build_default_requests_session() -> requests.Session:
    return requests.Session()


def _build_ipv4_requests_session() -> requests.Session:
    session = requests.Session()
    adapter = IPv4HTTPAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _send_push_with_session(
    *,
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
    session: requests.Session,
    connect_timeout_seconds: int,
) -> int:
    response = webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=vapid_private_key,
        vapid_claims=vapid_claims,
        content_encoding="aes128gcm",
        timeout=cast(Any, (connect_timeout_seconds, DEFAULT_PUSH_READ_TIMEOUT_SECONDS)),
        requests_session=session,
    )
    return response.status_code  # type: ignore[union-attr]


def _send_push_with_fallback(
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
) -> int:
    """Send using normal dual-stack resolution, then retry with IPv4-only on connect failures."""
    session = _build_default_requests_session()
    try:
        return _send_push_with_session(
            subscription_info=subscription_info,
            payload=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            session=session,
            connect_timeout_seconds=DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        )
    except (RequestsConnectTimeout, RequestsConnectionError) as exc:
        logger.info("Push delivery retrying via IPv4 after initial network failure: %s", exc)
    finally:
        session.close()

    session = _build_ipv4_requests_session()
    try:
        return _send_push_with_session(
            subscription_info=subscription_info,
            payload=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            session=session,
            connect_timeout_seconds=IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
        )
    finally:
        session.close()


async def send_push(
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
) -> int:
    """Send an encrypted push notification.

    Args:
        subscription_info: {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}
        payload: JSON string to encrypt and send
        vapid_private_key: base64url-encoded raw EC private key scalar
        vapid_claims: {"sub": "mailto:..."} or {"sub": "https://..."}

    Returns:
        HTTP status code from the push service.

    Raises:
        WebPushException: on push service error (caller handles 404/410 cleanup).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _send_push_with_fallback(
            subscription_info, payload, vapid_private_key, vapid_claims
        ),
    )
