from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domain.common import DomainModel


class SourceKind(StrEnum):
    # acquisition tier that produced a page or fact (plan 03 §14)
    site_direct = "site_direct"
    site_rendered = "site_rendered"
    reader_proxy = "reader_proxy"
    search_index = "search_index"
    archive = "archive"
    knowledge_base = "knowledge_base"
    web_mentions = "web_mentions"
    youtube_channel = "youtube_channel"
    user = "user"


CONFIRMATION_REQUIRED_SOURCES: frozenset[SourceKind] = frozenset(
    {SourceKind.archive, SourceKind.knowledge_base, SourceKind.web_mentions}
)


class ExtractionKind(StrEnum):
    page_text = "page_text"
    json_ld = "json_ld"
    meta = "meta"
    nav = "nav"
    url_inventory = "url_inventory"
    channel_metadata = "channel_metadata"
    user_input = "user_input"


class CoverageFacet(StrEnum):
    home = "home"
    offerings = "offerings"
    about = "about"
    contact_locations = "contact_locations"
    conversion_actions = "conversion_actions"


class FetchOutcome(StrEnum):
    ok = "ok"
    not_modified = "not_modified"
    blocked = "blocked"
    robots_disallowed = "robots_disallowed"
    not_found = "not_found"
    non_html = "non_html"
    too_large = "too_large"
    timeout = "timeout"
    error = "error"


class BlockReason(StrEnum):
    waf_block = "waf_block"
    cf_challenge = "cf_challenge"
    captcha = "captcha"
    login_required = "login_required"
    geo_block = "geo_block"
    rate_limited = "rate_limited"
    empty_shell = "empty_shell"
    other = "other"


class UserAgentMode(StrEnum):
    honest = "honest"
    compat = "compat"


class SiteRef(DomainModel):
    input_url: str
    canonical_origin: str
    registrable_domain: str
    scope_hosts: list[str]
    focus_path: str | None = None
    redirect_chain: list[str] = []


class FetchResult(DomainModel):
    requested_url: str
    final_url: str
    source_kind: SourceKind
    outcome: FetchOutcome
    status_code: int | None
    block_reason: BlockReason | None = None
    content_type: str | None = None
    html: str | None = None
    text: str | None = None
    json_ld: list[dict[str, Any]] = []
    outbound_links: list[str] = []
    redirect_chain: list[str] = []
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: AwareDatetime
    snapshot_at: AwareDatetime | None = None

    @property
    def blocked(self) -> bool:
        return self.outcome in {FetchOutcome.blocked, FetchOutcome.robots_disallowed}

    @model_validator(mode="after")
    def _archive_needs_snapshot(self) -> "FetchResult":
        if self.source_kind is SourceKind.archive and self.snapshot_at is None:
            raise ValueError("archive fetches must carry snapshot_at")
        return self


class CrawledPage(DomainModel):
    page_id: UUID
    url: str
    canonical_url: str
    final_url: str
    source_kind: SourceKind
    status_code: int | None
    content_type: str | None
    lang: str | None
    title: str | None
    meta_description: str | None = None
    h1: str | None = None
    headings: list[str] = []
    text: str = Field(default="", max_length=60_000)
    content_hash: str
    json_ld: list[dict[str, Any]] = []
    links_internal: list[str] = Field(default=[], max_length=300)
    youtube_refs: list[str] = []
    page_type: str | None = None
    template_key: str | None = None
    injection_flags: int = 0
    truncated: bool = False
    low_trust: bool = False
    fetched_at: AwareDatetime
    snapshot_at: AwareDatetime | None = None


class CrawlBudget(DomainModel):
    max_discovered: int = 25
    max_fetches: int = 90
    max_renders: int = 12
    max_pages_used: int = 60
    max_tavily_credits: int = 30
    max_archive_fetches: int = 20
    max_reader_fetches: int = 20
    wall_seconds: float = 300.0


class CrawlRunStatus(StrEnum):
    running = "running"
    completed = "completed"
    partial = "partial"
    blocked = "blocked"
    failed = "failed"


class CrawlRun(DomainModel):
    crawl_run_id: UUID
    site: SiteRef
    trigger: Literal["onboarding", "probe", "incremental", "full", "manual"]
    profile: Literal["client", "competitor"]
    status: CrawlRunStatus
    ua_mode: UserAgentMode
    pages: list[CrawledPage]
    sources_used: list[SourceKind]
    blocked: dict[str, int] = {}
    stats: dict[str, int] = {}
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
