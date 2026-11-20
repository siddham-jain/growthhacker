from typing import Protocol
from uuid import UUID

from pydantic import Field

from app.domain.common import DomainModel
from app.domain.web import CrawlBudget, CrawlRun, FetchResult, SiteRef, UserAgentMode
from app.ports.progress import ProgressReporter


class FetchContext(DomainModel):
    client_id: UUID | None
    run_id: UUID | None
    ua_mode: UserAgentMode = UserAgentMode.honest
    timeout_s: float = Field(default=20.0, gt=0)
    max_bytes: int = Field(default=5_000_000, gt=0)


class PageFetcher(Protocol):
    # obeys robots.txt and never solves captchas or rotates identities
    name: str

    async def fetch(self, url: str, ctx: FetchContext) -> FetchResult: ...


class Renderer(Protocol):
    # headless browser render for javascript shells; same robots and identity rules as PageFetcher
    name: str

    async def render(self, url: str, ctx: FetchContext) -> FetchResult: ...


class SiteCrawler(Protocol):
    async def crawl(
        self,
        site: SiteRef,
        budget: CrawlBudget,
        reporter: ProgressReporter,
        previous: CrawlRun | None = None,
    ) -> CrawlRun: ...

    async def fetch_page(self, url: str, ctx: FetchContext) -> FetchResult: ...
