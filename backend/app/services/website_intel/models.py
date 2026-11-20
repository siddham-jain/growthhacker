from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.domain.common import DomainModel
from app.domain.web import BlockReason, CrawledPage, FetchOutcome


class PageType(StrEnum):
    home = "home"
    about = "about"
    offering_hub = "offering_hub"
    offering_detail = "offering_detail"
    pricing = "pricing"
    locations_hub = "locations_hub"
    location_detail = "location_detail"
    faq = "faq"
    case_study = "case_study"
    team_hub = "team_hub"
    team_member = "team_member"
    contact = "contact"
    booking = "booking"
    blog_index = "blog_index"
    blog_post = "blog_post"
    knowledge_library = "knowledge_library"
    news_press = "news_press"
    careers = "careers"
    legal = "legal"
    investor = "investor"
    other = "other"


class PageVerdict(StrEnum):
    # per-response outcome from the block classifier (plan 03 §5.6)
    ok = "ok"
    not_modified = "not_modified"
    not_found = "not_found"
    gone = "gone"
    waf_block = "waf_block"
    cf_challenge = "cf_challenge"
    captcha = "captcha"
    rate_limited = "rate_limited"
    login_wall = "login_wall"
    geo_block = "geo_block"
    consent_wall = "consent_wall"
    js_shell = "js_shell"
    soft_404 = "soft_404"
    server_error = "server_error"
    error = "error"

    @property
    def is_block(self) -> bool:
        return self in BLOCK_REASON_BY_VERDICT

    @property
    def block_reason(self) -> BlockReason | None:
        return BLOCK_REASON_BY_VERDICT.get(self)

    @property
    def fetch_outcome(self) -> FetchOutcome:
        if self is PageVerdict.ok:
            return FetchOutcome.ok
        if self is PageVerdict.not_modified:
            return FetchOutcome.not_modified
        if self in {PageVerdict.not_found, PageVerdict.gone, PageVerdict.soft_404}:
            return FetchOutcome.not_found
        if self.is_block:
            return FetchOutcome.blocked
        return FetchOutcome.error


BLOCK_REASON_BY_VERDICT: dict[PageVerdict, BlockReason] = {
    PageVerdict.waf_block: BlockReason.waf_block,
    PageVerdict.cf_challenge: BlockReason.cf_challenge,
    PageVerdict.captcha: BlockReason.captcha,
    PageVerdict.rate_limited: BlockReason.rate_limited,
    PageVerdict.login_wall: BlockReason.login_required,
    PageVerdict.geo_block: BlockReason.geo_block,
    PageVerdict.consent_wall: BlockReason.other,
    PageVerdict.js_shell: BlockReason.empty_shell,
}


class LinkRegion(StrEnum):
    header = "header"
    nav = "nav"
    footer = "footer"
    body = "body"


class PageLink(DomainModel):
    url: str
    anchor: str = ""
    region: LinkRegion = LinkRegion.body


class NavItem(DomainModel):
    label: str
    url: str
    depth: int


class Hreflang(DomainModel):
    lang: str
    url: str


CtaType = Literal[
    "book_appointment",
    "call",
    "whatsapp",
    "demo",
    "signup",
    "free_trial",
    "buy",
    "quote_request",
    "contact_form",
    "download",
    "newsletter",
    "app_install",
    "visit_location",
    "email",
    "other",
]
CtaMedium = Literal["web", "phone", "whatsapp", "email", "app", "in_person"]


class CtaCandidate(DomainModel):
    type: CtaType
    label: str
    target: str
    medium: CtaMedium
    page_url: str
    in_header: bool = False
    in_footer: bool = False

    @property
    def key(self) -> str:
        return f"{self.type}|{self.target}"


PricePeriod = Literal["one_time", "per_month", "per_year", "per_session", "per_unit", "unknown"]
PriceQualifier = Literal["exact", "from", "range", "discounted", "free", "contact_for_price", "unknown"]


class PriceCandidate(DomainModel):
    raw_text: str
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    currency: str | None = None
    period: PricePeriod = "unknown"
    qualifier: PriceQualifier = "unknown"
    context: str = ""
    heading: str | None = None
    origin: Literal["text", "json_ld"] = "text"
    page_url: str


class PageDetails(DomainModel):
    # deterministic signals kept beside a CrawledPage; the domain page has no slot for them
    page_id: UUID
    links: list[PageLink] = []
    nav: list[NavItem] = []
    ctas: list[CtaCandidate] = []
    prices: list[PriceCandidate] = []
    hreflang: list[Hreflang] = []
    meta: dict[str, str] = {}
    youtube_embeds: list[str] = []
    published_at: AwareDatetime | None = None
    simhash: int | None = None
    pdf_links: list[PageLink] = []


class ExtractedPage(DomainModel):
    page: CrawledPage
    details: PageDetails


UrlSource = Literal["sitemap", "nav", "link", "tavily_map", "user", "home"]


@dataclass
class UrlCandidate:
    url: str
    source: UrlSource
    depth: int = 0
    anchor: str = ""
    nav_depth: int | None = None
    linked_from_home: bool = False
    inlinks: int = 0
    lastmod: datetime | None = None
    sitemap_priority: float | None = None
    page_type: PageType = PageType.other
    template_key: str | None = None
    language_alternate: bool = False
    under_focus: bool = False
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class InventoryUrl(DomainModel):
    url: str
    page_type: PageType
    template_key: str | None = None
    source: UrlSource
    lastmod: AwareDatetime | None = None


class TemplateSummary(DomainModel):
    template_key: str
    count: int
    variable_values: list[str] = []
    location_values: list[str] = []
