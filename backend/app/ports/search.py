from typing import Literal, Protocol

from pydantic import AwareDatetime, Field

from app.domain.common import DomainModel


class WebSearchQuery(DomainModel):
    query: str
    topic: Literal["general", "news"] = "general"
    time_range: Literal["day", "week", "month", "year"] | None = None
    include_domains: list[str] = []
    exclude_domains: list[str] = []
    max_results: int = Field(default=10, ge=1, le=20)
    search_depth: Literal["basic", "advanced"] = "basic"
    include_raw_content: bool = False
    country: str | None = None


class WebSearchHit(DomainModel):
    url: str
    title: str
    snippet: str
    provider_score: float | None = None
    published_at: AwareDatetime | None = None
    raw_content: str | None = None


class WebSearchResponse(DomainModel):
    query: WebSearchQuery
    hits: list[WebSearchHit]
    credits_used: float
    from_cache: bool
    fetched_at: AwareDatetime


class ExtractRequest(DomainModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    depth: Literal["basic", "advanced"] = "basic"
    format: Literal["markdown", "text"] = "markdown"
    query: str | None = None


class SiteMapRequest(DomainModel):
    url: str
    instructions: str | None = None
    max_depth: int = Field(default=1, ge=1, le=5)
    max_breadth: int = Field(default=20, ge=1, le=500)
    limit: int = Field(default=50, ge=1)
    select_paths: list[str] = []
    exclude_paths: list[str] = []


class ExtractedDocument(DomainModel):
    url: str
    raw_content: str
    published_at: AwareDatetime | None = None


class FailedExtraction(DomainModel):
    url: str
    error: str


class ExtractResponse(DomainModel):
    results: list[ExtractedDocument]
    failed: list[FailedExtraction] = []
    credits_used: float
    fetched_at: AwareDatetime


class SiteMapResponse(DomainModel):
    base_url: str
    urls: list[str]
    credits_used: float
    fetched_at: AwareDatetime


class WebSearch(Protocol):
    # callers pass only urls that our own robots check allows; credits are metered by the adapter
    async def search(self, query: WebSearchQuery) -> WebSearchResponse: ...

    async def extract(self, request: ExtractRequest) -> ExtractResponse: ...

    async def map(self, request: SiteMapRequest) -> SiteMapResponse: ...

    async def crawl(self, request: SiteMapRequest) -> ExtractResponse: ...
