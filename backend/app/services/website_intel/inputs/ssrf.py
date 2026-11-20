import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

ALLOWED_PORTS = frozenset({80, 443})
CGNAT = ipaddress.ip_network("100.64.0.0/10")
METADATA_ADDRESSES = frozenset(
    {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeTargetError(ValueError):
    user_message = "We can only scan public websites."


class DnsLookupError(OSError):
    user_message = (
        "We couldn't find this domain. Check the spelling or continue with your YouTube channel only."
    )


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise DnsLookupError(f"cannot resolve {host}") from error
    return sorted({info[4][0] for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in METADATA_ADDRESSES or (isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


class SsrfGuard:
    """rejects non-public targets; called on every redirect hop and every browser sub-request."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolver = resolver

    async def check(self, url: str) -> list[str]:
        """the vetted addresses for the url's host; pin them so the connection cannot be re-resolved."""
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise UnsafeTargetError(f"unsupported target {url!r}")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise UnsafeTargetError(f"port {port} is not allowed")
        addresses = await self._resolver(parts.hostname, port)
        if not addresses:
            raise DnsLookupError(f"no addresses for {parts.hostname}")
        unsafe = [address for address in addresses if not is_public_address(address)]
        if unsafe:
            raise UnsafeTargetError(f"{parts.hostname} resolves to non-public {unsafe[0]}")
        return addresses


_pins: ContextVar[Mapping[str, tuple[str, ...]]] = ContextVar("ssrf_pins", default=MappingProxyType({}))


def _pin_key(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return host.lower()


@contextmanager
def pinned(url: str, addresses: Sequence[str] | None) -> Iterator[None]:
    """new connections to the url's host made inside this block go to the vetted addresses only."""
    host = urlsplit(url).hostname
    if not host or not addresses:
        yield
        return
    token = _pins.set({**_pins.get(), _pin_key(host): tuple(addresses)})
    try:
        yield
    finally:
        _pins.reset(token)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """connects pinned hosts to their vetted ip; tls sni and the host header still use the hostname."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109  (httpcore's backend interface)
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = _pins.get().get(_pin_key(host), (host,))
        failure: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as error:
                failure = error
        assert failure is not None
        raise failure

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109  (httpcore's backend interface)
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport that honours pinned(); unpinned hosts resolve as usual."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # httpx exposes no network backend option, so the pool's backend is wrapped in place
        self._pool._network_backend = PinnedNetworkBackend(self._pool._network_backend)
