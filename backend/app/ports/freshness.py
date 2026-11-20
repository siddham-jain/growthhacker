from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import AwareDatetime

from app.domain.common import DomainModel


class SourceCapabilities(DomainModel):
    supports_time_filter: bool
    supports_language: bool
    supports_region: bool
    returns_dates: Literal["datetime", "date", "mixed", "none"]
    cost_unit: Literal["tavily_credit", "free"]
    min_interval_seconds: float
    content_policy: Literal["snippet_ok", "metadata_and_link_only"]


class PlannedQuery(DomainModel):
    source_key: str
    normalized_query: str
    params: dict[str, Any] = {}
    query_hash: str
    cadence_minutes: int


class RawNewsItem(DomainModel):
    url: str
    title: str
    snippet: str
    published_at: AwareDatetime | None
    language: str | None = None
    provider_score: float | None = None
    external_ids: dict[str, str] = {}


class NewsFetchResult(DomainModel):
    items: list[RawNewsItem]
    cost: float
    status: Literal["ok", "partial", "rate_limited", "exhausted", "error"]
    error: str | None = None
    provider_request_id: str | None = None


class SourceHealth(DomainModel):
    healthy: bool
    detail: str | None = None
    checked_at: AwareDatetime


class FreshnessSource(Protocol):
    key: str
    capabilities: SourceCapabilities

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult: ...

    async def health(self) -> SourceHealth: ...
