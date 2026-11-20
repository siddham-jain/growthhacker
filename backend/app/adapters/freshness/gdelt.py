import math
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.adapters.freshness.base import FreshnessDeps, clean_text, failed_result, get_json, respect_interval
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 50
MAX_TIMESPAN_DAYS = 90


@freshness_source_registry.register("gdelt")
class GdeltDocSource:
    key: ClassVar[str] = "gdelt"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=True,
        supports_language=True,
        supports_region=True,
        returns_dates="datetime",
        cost_unit="free",
        min_interval_seconds=5.0,
        content_policy="metadata_and_link_only",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        text = query.normalized_query
        country = query.params.get("source_country")
        if country:
            text = f"{text} sourcecountry:{country}"
        days = math.ceil((self.deps.clock.now() - since).total_seconds() / 86_400)
        params: dict[str, str | int] = {
            "query": text,
            "mode": "artlist",
            "format": "json",
            "maxrecords": MAX_RECORDS,
            "timespan": f"{min(max(days, 1), MAX_TIMESPAN_DAYS)}d",
            "sort": "datedesc",
        }
        await respect_interval(self.deps, self.key, self.capabilities.min_interval_seconds)
        try:
            response = await get_json(self.deps, GDELT_DOC_API, params)
        except httpx.HTTPError as error:
            return failed_result(None, f"gdelt request failed: {type(error).__name__}")
        if response.status_code != 200:
            return failed_result(response.status_code, f"gdelt http {response.status_code}")
        try:
            articles: list[dict[str, Any]] = response.json().get("articles", [])
        except ValueError:
            # gdelt answers rate limits with a plain-text 200 body
            if "limit requests" in response.text.lower():
                return failed_result(429, "gdelt rate limited")
            return failed_result(None, "gdelt returned non-json")
        items = []
        for article in articles:
            published_at = _parse_seendate(article.get("seendate"))
            if not article.get("url") or (published_at is not None and published_at < since):
                continue
            items.append(
                RawNewsItem(
                    url=article["url"],
                    title=clean_text(article.get("title"), 500),
                    snippet="",
                    published_at=published_at,
                )
            )
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())


def _parse_seendate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
