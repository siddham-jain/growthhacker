import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

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

    async def check(self, url: str) -> None:
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
