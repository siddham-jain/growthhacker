import re
from datetime import datetime
from typing import ClassVar

import feedparser
import httpx

from app.adapters.freshness.base import FreshnessDeps, clean_text, failed_result, get_json, respect_interval
from app.adapters.freshness.rss import entry_published_at
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

ARXIV_API = "https://export.arxiv.org/api/query"
DEFAULT_CATEGORIES = ("cs.CL", "cs.AI", "cs.LG")
ARXIV_ID_PATTERN = re.compile(r"arxiv\.org/abs/([^v\s]+(?:v\d+)?)")
MAX_RESULTS = 25


@freshness_source_registry.register("arxiv")
class ArxivSource:
    key: ClassVar[str] = "arxiv"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=False,
        supports_language=False,
        supports_region=False,
        returns_dates="datetime",
        cost_unit="free",
        min_interval_seconds=3.0,
        content_policy="snippet_ok",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        categories = query.params.get("categories") or list(DEFAULT_CATEGORIES)
        category_clause = " OR ".join(f"cat:{category}" for category in categories)
        phrase = query.normalized_query.replace('"', "")
        params: dict[str, str | int] = {
            "search_query": f'({category_clause}) AND all:"{phrase}"',
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": MAX_RESULTS,
        }
        await respect_interval(self.deps, self.key, self.capabilities.min_interval_seconds)
        try:
            response = await get_json(self.deps, ARXIV_API, params)
        except httpx.HTTPError as error:
            return failed_result(None, f"arxiv request failed: {type(error).__name__}")
        if response.status_code != 200:
            return failed_result(response.status_code, f"arxiv http {response.status_code}")
        items = []
        for entry in feedparser.parse(response.content).entries:
            published_at = entry_published_at(entry)
            link = entry.get("link") or entry.get("id")
            if not link or published_at is None or published_at < since:
                continue
            match = ARXIV_ID_PATTERN.search(entry.get("id", "") or link)
            items.append(
                RawNewsItem(
                    url=link,
                    title=clean_text(entry.get("title"), 500),
                    snippet=clean_text(entry.get("summary")),
                    published_at=published_at,
                    external_ids={"arxiv_id": match.group(1)} if match else {},
                )
            )
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())
