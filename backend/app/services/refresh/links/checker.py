import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx
import regex
from protego import Protego

from app.core.config import RefreshSettings
from app.ports.clock import Clock
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.links.urls import malformation, normalise_url, on_domains

USER_AGENT = "GrowthHackerLinkCheck/1.0"
DEAD_STATUSES = frozenset({404, 410})
BLOCKED_STATUSES = frozenset({401, 403, 429, 999})
METHOD_FALLBACK_STATUSES = frozenset({405, 501})
PERMANENT_REDIRECTS = frozenset({301, 308})
NOT_FOUND_TITLE = regex.compile(
    r"<title[^>]*>[^<]*(not found|404|page missing)[^<]*</title>", regex.IGNORECASE
)
FIRST_5XX = "server error on first probe"
CGNAT = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], Awaitable[list[str]]]


class LinkProbe(Protocol):
    async def probe(self, url: str, previous: LinkStatus | None = None) -> LinkStatus: ...


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list({info[4][0] for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT)
    )


class UnsafeTarget(Exception):
    pass


class HostPacer:
    """at most one request per host per interval."""

    def __init__(self, interval_s: float, sleep: Callable[[float], Awaitable[None]]) -> None:
        self.interval_s = interval_s
        self._sleep = sleep
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def __call__(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                wait = self.interval_s - (time.monotonic() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[host] = time.monotonic()


class LinkChecker:
    """http(s) only, ssrf-safe on every hop, head first, never treats a blocked or slow site as broken."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: RefreshSettings,
        clock: Clock,
        *,
        own_domains: frozenset[str] = frozenset(),
        resolver: Resolver = system_resolver,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.settings = settings
        self.clock = clock
        self.own_domains = own_domains
        self.resolver = resolver
        self.pacer = HostPacer(settings.link_domain_interval_s, sleep)
        self._slots = asyncio.Semaphore(settings.link_concurrency)
        self._robots: dict[str, Protego | None] = {}

    async def probe(self, url: str, previous: LinkStatus | None = None) -> LinkStatus:
        key = normalise_url(url)
        problem = malformation(url)
        if problem is not None:
            return self._status(
                key, LinkStatusClass.malformed, error=problem.reason, repaired=problem.repaired
            )
        async with self._slots:
            try:
                return await self._follow(url, key, previous)
            except UnsafeTarget as error:
                return self._status(key, LinkStatusClass.unverifiable, error=str(error))
            except httpx.TimeoutException:
                return self._status(key, LinkStatusClass.unverifiable, error="timed out")
            except httpx.ConnectError as error:
                return self._status(key, LinkStatusClass.dead, error=f"connection failed: {error}")
            except (httpx.HTTPError, OSError) as error:
                return self._status(key, LinkStatusClass.unverifiable, error=type(error).__name__)

    async def _guard(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise UnsafeTarget("only http and https links are checked")
        try:
            addresses = await self.resolver(parts.hostname)
        except socket.gaierror as error:
            raise httpx.ConnectError(f"cannot resolve {parts.hostname}") from error
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise UnsafeTarget("link points at a private or internal address")

    async def _follow(self, url: str, key: str, previous: LinkStatus | None) -> LinkStatus:
        current = url
        first_status: int | None = None
        for _ in range(self.settings.link_max_redirects + 1):
            await self._guard(current)
            if not await self._robots_allow(current):
                return self._status(key, LinkStatusClass.unverifiable, error="robots.txt disallows checking")
            await self.pacer(urlsplit(current).hostname or "")
            response = await self._request(current)
            first_status = first_status or response.status_code
            if response.is_redirect and "location" in response.headers:
                target = urljoin(current, response.headers["location"])
                if (
                    response.status_code in PERMANENT_REDIRECTS
                    and current == url
                    and not self._same_page(url, target)
                ):
                    return self._status(
                        key, LinkStatusClass.redirect, http_status=response.status_code, final=target
                    )
                if on_domains(url, self.own_domains) and self._soft_404_redirect(url, target):
                    return self._status(
                        key, LinkStatusClass.soft_404, http_status=response.status_code, final=target
                    )
                current = target
                continue
            return self._classify(key, url, current, response, previous)
        return self._status(
            key, LinkStatusClass.unverifiable, http_status=first_status, error="too many redirects"
        )

    async def _request(self, url: str) -> httpx.Response:
        headers = {"User-Agent": USER_AGENT}
        timeout = self.settings.link_timeout_s
        response = await self.client.head(url, headers=headers, timeout=timeout, follow_redirects=False)
        own_domain = on_domains(url, self.own_domains)
        if response.status_code in METHOD_FALLBACK_STATUSES or (own_domain and response.status_code == 200):
            async with self.client.stream(
                "GET", url, headers=headers, timeout=timeout, follow_redirects=False
            ) as streamed:
                body = b""
                if own_domain and streamed.status_code == 200:
                    async for chunk in streamed.aiter_bytes():
                        body += chunk
                        if len(body) > 16_384:
                            break
                return httpx.Response(streamed.status_code, headers=streamed.headers, content=body)
        return response

    def _classify(
        self, key: str, original: str, final: str, response: httpx.Response, previous: LinkStatus | None
    ) -> LinkStatus:
        code = response.status_code
        final_url = final if final != original else None
        if code in DEAD_STATUSES:
            return self._status(key, LinkStatusClass.dead, http_status=code, final=final_url)
        if code in BLOCKED_STATUSES:
            return self._status(key, LinkStatusClass.unverifiable, http_status=code, final=final_url)
        if code >= 500:
            gap_h = self.settings.link_recheck_5xx_hours
            if (
                previous is not None
                and previous.error == FIRST_5XX
                and (self.clock.now() - previous.checked_at).total_seconds() >= gap_h * 3600
            ):
                return self._status(key, LinkStatusClass.dead, http_status=code, final=final_url)
            return self._status(key, LinkStatusClass.unverifiable, http_status=code, error=FIRST_5XX)
        if code == 200 and on_domains(original, self.own_domains) and NOT_FOUND_TITLE.search(response.text):
            return self._status(key, LinkStatusClass.soft_404, http_status=code, final=final_url)
        if 200 <= code < 400:
            return self._status(key, LinkStatusClass.ok, http_status=code, final=final_url)
        return self._status(key, LinkStatusClass.unverifiable, http_status=code, final=final_url)

    async def _robots_allow(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._fetch_robots(origin)
        rules = self._robots[origin]
        return rules is None or rules.can_fetch(url, USER_AGENT)

    async def _fetch_robots(self, origin: str) -> Protego | None:
        try:
            await self._guard(origin)
            response = await self.client.get(
                f"{origin}/robots.txt",
                headers={"User-Agent": USER_AGENT},
                timeout=self.settings.link_timeout_s,
                follow_redirects=False,
            )
        except (httpx.HTTPError, UnsafeTarget, OSError):
            return None
        if response.status_code != 200:
            return None
        return Protego.parse(response.text)

    @staticmethod
    def _same_page(original: str, target: str) -> bool:
        return normalise_url(original) == normalise_url(target)

    @staticmethod
    def _soft_404_redirect(original: str, target: str) -> bool:
        source, destination = urlsplit(original), urlsplit(target)
        deep = len([part for part in source.path.split("/") if part]) >= 2
        root = destination.path in ("", "/")
        return deep and root and normalise_url(original).split("/")[0] == normalise_url(target).split("/")[0]

    def _status(
        self,
        key: str,
        status_class: LinkStatusClass,
        *,
        http_status: int | None = None,
        final: str | None = None,
        error: str | None = None,
        repaired: str | None = None,
    ) -> LinkStatus:
        return LinkStatus(
            url_normalised=key,
            status_class=status_class,
            checked_at=self.clock.now(),
            http_status=http_status,
            final_url=final,
            error=error,
            repaired_url=repaired,
        )
