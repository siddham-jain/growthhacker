import calendar
import time
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urljoin

import feedparser
import httpx

from app.adapters.freshness.base import FreshnessDeps, clean_text, failed_result, respect_interval
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)
from app.services.website_intel.inputs.ssrf import (
    DnsLookupError,
    PinnedTransport,
    SsrfGuard,
    UnsafeTargetError,
    pinned,
)

MAX_REDIRECTS = 3
MAX_FEED_BYTES = 5_000_000


class FeedTooLarge(Exception):
    pass


def struct_to_datetime(value: time.struct_time | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)


def entry_published_at(entry: Any) -> datetime | None:
    return struct_to_datetime(entry.get("published_parsed") or entry.get("updated_parsed"))


async def _read_capped(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_FEED_BYTES:
            raise FeedTooLarge(f"feed is over {MAX_FEED_BYTES} bytes")
    return bytes(body)


async def fetch_feed(url: str, guard: SsrfGuard) -> tuple[int, bytes]:
    """status and body; every redirect hop is ssrf-checked and pinned, and the body is size-capped."""
    async with httpx.AsyncClient(
        transport=PinnedTransport(), follow_redirects=False, timeout=httpx.Timeout(20.0, connect=5.0)
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            addresses = await guard.check(current)
            with pinned(current, addresses):
                async with client.stream("GET", current) as response:
                    location = response.headers.get("location")
                    if response.is_redirect and location:
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        return response.status_code, b""
                    return 200, await _read_capped(response)
    raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


@freshness_source_registry.register("rss")
class RssSource:
    key: ClassVar[str] = "rss"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=False,
        supports_language=False,
        supports_region=False,
        returns_dates="mixed",
        cost_unit="free",
        min_interval_seconds=1.0,
        content_policy="snippet_ok",
    )

    def __init__(self, deps: FreshnessDeps, guard: SsrfGuard | None = None) -> None:
        self.deps = deps
        self.guard = guard or SsrfGuard()

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        url = query.params.get("url")
        if not url:
            return failed_result(None, "rss query needs params.url")
        await respect_interval(
            self.deps, f"{self.key}:{query.normalized_query}", self.capabilities.min_interval_seconds
        )
        try:
            status, body = await fetch_feed(url, self.guard)
        except (UnsafeTargetError, DnsLookupError):
            return failed_result(None, "rss feed is not on a public address")
        except FeedTooLarge:
            return failed_result(None, f"rss feed is over {MAX_FEED_BYTES} bytes")
        except httpx.HTTPError as error:
            return failed_result(None, f"rss request failed: {type(error).__name__}")
        if status != 200:
            return failed_result(status, f"rss http {status}")
        feed = feedparser.parse(body)
        if feed.bozo and not feed.entries:
            return failed_result(None, "rss feed could not be parsed")
        items = []
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            published_at = entry_published_at(entry)
            if published_at is not None and published_at < since:
                continue
            items.append(
                RawNewsItem(
                    url=link,
                    title=clean_text(entry.get("title"), 500),
                    snippet=clean_text(entry.get("summary")),
                    published_at=published_at,
                )
            )
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())
