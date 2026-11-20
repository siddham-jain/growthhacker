from datetime import datetime
from typing import Any, ClassVar

import httpx

from app.adapters.freshness.base import (
    FreshnessDeps,
    clean_text,
    failed_result,
    get_json,
    parse_iso,
    respect_interval,
)
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

WHO_DON_API = "https://www.who.int/api/news/diseaseoutbreaknews"
WHO_DON_ITEM_URL = "https://www.who.int/emergencies/disease-outbreak-news/item/"


@freshness_source_registry.register("who_don")
class WhoOutbreakNewsSource:
    key: ClassVar[str] = "who_don"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=False,
        supports_language=False,
        supports_region=False,
        returns_dates="datetime",
        cost_unit="free",
        min_interval_seconds=1.0,
        content_policy="metadata_and_link_only",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        params: dict[str, str | int] = {
            "$orderby": "PublicationDate desc",
            "$top": 20,
            "$select": "Title,PublicationDate,ItemDefaultUrl,DonId",
        }
        await respect_interval(self.deps, self.key, self.capabilities.min_interval_seconds)
        try:
            response = await get_json(self.deps, WHO_DON_API, params)
            if response.status_code != 200:
                return failed_result(response.status_code, f"who http {response.status_code}")
            records: list[dict[str, Any]] = response.json().get("value", [])
        except (httpx.HTTPError, ValueError) as error:
            return failed_result(None, f"who request failed: {type(error).__name__}")
        items = []
        for record in records:
            path = str(record.get("ItemDefaultUrl") or "").lstrip("/")
            published_at = parse_iso(record.get("PublicationDate"))
            if not path or (published_at is not None and published_at < since):
                continue
            items.append(
                RawNewsItem(
                    url=WHO_DON_ITEM_URL + path,
                    title=clean_text(record.get("Title"), 500),
                    snippet="",
                    published_at=published_at,
                    language="en",
                    external_ids={"don_id": str(record["DonId"])} if record.get("DonId") else {},
                )
            )
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())
