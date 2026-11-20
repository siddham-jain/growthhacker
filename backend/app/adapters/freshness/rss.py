import calendar
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

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

MAX_REDIRECTS = 3


def struct_to_datetime(value: time.struct_time | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)


def entry_published_at(entry: Any) -> datetime | None:
    return struct_to_datetime(entry.get("published_parsed") or entry.get("updated_parsed"))


async def fetch_feed(url: str) -> httpx.Response:
    # feeds move; follow at most a few redirects on a short-lived client
    async with httpx.AsyncClient(
        follow_redirects=True, max_redirects=MAX_REDIRECTS, timeout=httpx.Timeout(20.0, connect=5.0)
    ) as client:
        return await client.get(url)


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

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        url = query.params.get("url")
        if not url:
            return failed_result(None, "rss query needs params.url")
        await respect_interval(
            self.deps, f"{self.key}:{query.normalized_query}", self.capabilities.min_interval_seconds
        )
        try:
            response = await fetch_feed(url)
        except httpx.HTTPError as error:
            return failed_result(None, f"rss request failed: {type(error).__name__}")
        if response.status_code != 200:
            return failed_result(response.status_code, f"rss http {response.status_code}")
        feed = feedparser.parse(response.content)
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
