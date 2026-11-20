from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.domain.common import ChannelId, CountryCode, DomainModel, Priority, UnitInterval
from app.domain.news import EventType
from app.domain.web import CONFIRMATION_REQUIRED_SOURCES, ExtractionKind, SiteRef, SourceKind
from app.domain.youtube import OwnershipVerdict

IndustryCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class ClaimOrigin(StrEnum):
    extracted = "extracted"
    deterministic = "deterministic"
    derived = "derived"
    inferred = "inferred"
    user = "user"


class ClaimStatus(StrEnum):
    proposed = "proposed"
    needs_confirmation = "needs_confirmation"
    confirmed = "confirmed"
    edited = "edited"
    rejected = "rejected"
    stale = "stale"


class ReviewStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    rejected = "rejected"
    retired = "retired"


class FunnelStage(StrEnum):
    awareness = "awareness"
    consideration = "consideration"
    decision = "decision"
    post_purchase = "post_purchase"


class Evidence(DomainModel):
    page_id: UUID | None = None
    source_kind: SourceKind
    extraction: ExtractionKind
    source_url: str
    quote: str = Field(max_length=300)
    quote_lang: str | None = None
    char_start: int | None = None
    match_score: float = Field(ge=0.0, le=100.0)
    fetched_at: AwareDatetime
    snapshot_at: AwareDatetime | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.source_kind in CONFIRMATION_REQUIRED_SOURCES

    @model_validator(mode="after")
    def _archive_needs_snapshot(self) -> "Evidence":
        if self.source_kind is SourceKind.archive and self.snapshot_at is None:
            raise ValueError("archived evidence must carry snapshot_at")
        return self


class FactKind(StrEnum):
    offering = "offering"
    price_binding = "price_binding"
    value_prop = "value_prop"
    audience_statement = "audience_statement"
    location = "location"
    conversion = "conversion"
    faq = "faq"
    entity = "entity"
    brand_term = "brand_term"
    keyword_phrase = "keyword_phrase"
    competitor_mention = "competitor_mention"
    freshness_cue = "freshness_cue"
    credential = "credential"
    statistic = "statistic"


class Fact(Evidence):
    # a verified claim plus the verbatim quote and provenance that support it
    fact_id: Annotated[str, StringConstraints(pattern=r"^F\d{4,}$")]
    kind: FactKind
    text: str = Field(max_length=160)
    attrs: dict[str, str] = {}


class ClaimMeta(DomainModel):
    origin: ClaimOrigin
    status: ClaimStatus
    confidence: UnitInterval
    evidence: list[Evidence] = []
    basis_ids: list[str] = []
    locked: bool = False
    first_seen_version: int
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _provenance_required(self) -> "ClaimMeta":
        if self.origin in {ClaimOrigin.extracted, ClaimOrigin.deterministic} and not self.evidence:
            raise ValueError(f"{self.origin.value} claims need at least one evidence item")
        if self.origin in {ClaimOrigin.derived, ClaimOrigin.inferred} and not self.basis_ids:
            raise ValueError(f"{self.origin.value} claims need basis_ids")
        return self


class RelatedBrand(DomainModel):
    name: str
    domain: str | None = None
    relation: Literal["parent", "sister", "subsidiary", "unknown"]
    meta: ClaimMeta


class ChannelLink(DomainModel):
    channel_id: ChannelId
    handle: str | None = None
    ownership: OwnershipVerdict
    resolved_via: Literal["id", "forHandle", "forUsername", "video", "user"]


class BusinessModel(StrEnum):
    local_service = "local_service"
    multi_location_service = "multi_location_service"
    b2b_saas = "b2b_saas"
    b2c_app = "b2c_app"
    ecommerce = "ecommerce"
    marketplace = "marketplace"
    media_publisher = "media_publisher"
    manufacturer = "manufacturer"
    agency = "agency"
    nonprofit = "nonprofit"
    other = "other"


class BusinessIdentity(DomainModel):
    name: str
    name_meta: ClaimMeta
    legal_name: str | None = None
    one_liner: str = Field(max_length=160)
    one_liner_meta: ClaimMeta
    description: str | None = Field(default=None, max_length=600)
    industry: IndustryCode
    industry_meta: ClaimMeta
    niche: str = Field(max_length=80)
    business_model: BusinessModel
    geo_scope: Literal["local", "regional", "national", "multinational", "global_online"]
    primary_language: str
    languages: list[str]
    country: CountryCode | None = None
    timezone: str | None = None
    website: SiteRef | None
    website_domains: list[str] = []
    brand_aliases: list[str] = []
    primary_markets: list[CountryCode] = []
    channel: ChannelLink | None = None
    related_brands: list[RelatedBrand] = []
    country_sites: list[SiteRef] = []


class PriceSignal(DomainModel):
    raw_text: str
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    currency: str | None = None
    period: Literal["one_time", "per_month", "per_year", "per_session", "per_unit", "unknown"]
    qualifier: Literal["exact", "from", "range", "discounted", "free", "contact_for_price", "unknown"]
    meta: ClaimMeta


class Offering(DomainModel):
    id: str
    name: str
    name_original: str | None = None
    kind: Literal[
        "service",
        "product",
        "package",
        "plan",
        "program",
        "specialty",
        "department",
        "procedure",
        "feature",
        "other",
    ]
    description: str | None = Field(default=None, max_length=400)
    definition: str | None = None
    aliases: list[str] = []
    priority: Priority = 2
    url: str | None = None
    parent_id: str | None = None
    price_signals: list[PriceSignal] = []
    audience_ids: list[str] = []
    location_ids: list[str] = []
    is_primary: bool = False
    meta: ClaimMeta


class ValueProposition(DomainModel):
    id: str
    statement: str = Field(max_length=200)
    kind: Literal[
        "differentiator",
        "outcome",
        "proof_point",
        "credential",
        "accessibility",
        "pricing",
        "speed",
        "trust",
        "scale",
        "technology",
        "other",
    ]
    proof_points: list[str] = []
    offering_ids: list[str] = []
    meta: ClaimMeta


class StatedAudience(DomainModel):
    # as stated by the business; never inferred demographics
    id: str
    segment: str = Field(max_length=120)
    kind: Literal["business_role", "business_type", "condition_or_need", "use_case", "geography", "other"]
    meta: ClaimMeta


class Location(DomainModel):
    id: str
    name: str
    kind: Literal["hospital", "clinic", "branch", "store", "office", "service_area", "virtual", "other"]
    address: str | None = None
    city: str | None = None
    region: str | None = None
    country: CountryCode | None = None
    phone: str | None = None
    url: str | None = None
    geo: tuple[float, float] | None = None
    aliases: list[str] = []
    meta: ClaimMeta


class LocationSummary(DomainModel):
    count: int = 0
    cities: list[str] = []
    regions: list[str] = []
    countries: list[CountryCode] = []
    items: list[Location] = []


class Prominence(DomainModel):
    pages_seen: int
    on_home: bool
    in_header: bool
    in_footer: bool


class ConversionAction(DomainModel):
    id: str
    type: Literal[
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
    label: str
    target: str
    medium: Literal["web", "phone", "whatsapp", "email", "app", "in_person"]
    prominence: Prominence
    is_primary: bool = False
    offering_ids: list[str] = []
    location_ids: list[str] = []
    meta: ClaimMeta


class BrandTerm(DomainModel):
    term: str
    kind: Literal["company", "product", "sub_brand", "program", "slogan", "trademark"]
    variants: list[str] = []
    meta: ClaimMeta


class NicheKeyword(DomainModel):
    id: str
    term: str
    language: str
    script: str | None = None
    kind: Literal[
        "offering",
        "problem",
        "procedure",
        "technology",
        "category",
        "location_modifier",
        "intent_modifier",
        "brand_adjacent",
    ]
    tier: Literal["seed", "expanded"]
    source: Literal[
        "page_text",
        "title",
        "nav",
        "json_ld",
        "url_inventory",
        "youtube_channel_keywords",
        "user",
        "llm_expansion",
    ]
    weight: UnitInterval
    offering_ids: list[str] = []
    meta: ClaimMeta


class BuyerQuestionIntent(StrEnum):
    cost = "cost"
    comparison = "comparison"
    eligibility = "eligibility"
    procedure_how = "procedure_how"
    outcome_risk = "outcome_risk"
    recovery = "recovery"
    location_access = "location_access"
    booking_logistics = "booking_logistics"
    trust_credentials = "trust_credentials"
    how_to_use = "how_to_use"
    troubleshooting = "troubleshooting"
    other = "other"


class BuyerQuestionSourceKind(StrEnum):
    website_faq = "website_faq"
    website_derived = "website_derived"
    user = "user"
    spotlit = "spotlit"
    comments = "comments"
    import_ = "import"


class BuyerQuestion(DomainModel):
    id: UUID
    client_id: UUID
    text: str
    normalized_text: str
    language: str
    funnel_stage: FunnelStage
    intent: BuyerQuestionIntent
    offering_ids: list[str] = []
    conversion_action_id: str | None = None
    geo_modifier: str | None = None
    source: BuyerQuestionSourceKind
    source_ref: str | None = None
    status: ReviewStatus
    priority: Priority
    ai_cited_video_ids: list[str] | None = None
    ai_citations_observed_at: AwareDatetime | None = None
    meta: ClaimMeta


class CompetitorHint(DomainModel):
    name: str
    domain: str | None = None
    url: str | None = None
    hint_type: Literal["named_on_site", "comparison_page", "alternative_mention", "category_peer_inferred"]
    meta: ClaimMeta


class ContentPillar(DomainModel):
    id: str
    name: str
    description: str
    offering_ids: list[str] = []
    keyword_ids: list[str] = []
    buyer_question_ids: list[UUID] = []
    meta: ClaimMeta


class FreshnessSensitivity(StrEnum):
    very_low = "very_low"
    low = "low"
    medium = "medium"
    high = "high"
    very_high = "very_high"


class OperatingLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


OPERATING_LEVEL_BY_SENSITIVITY: dict[FreshnessSensitivity, OperatingLevel] = {
    FreshnessSensitivity.very_high: OperatingLevel.high,
    FreshnessSensitivity.high: OperatingLevel.high,
    FreshnessSensitivity.medium: OperatingLevel.medium,
    FreshnessSensitivity.low: OperatingLevel.low,
    FreshnessSensitivity.very_low: OperatingLevel.none,
}


class FreshnessDriver(DomainModel):
    statement: str
    meta: ClaimMeta


class WatchEntity(DomainModel):
    name: str
    aliases: list[str] = []
    kind: Literal[
        "company",
        "product",
        "model",
        "technology",
        "regulator",
        "standard_body",
        "guideline",
        "drug",
        "procedure",
        "condition",
        "event",
        "publication",
        "program",
        "competitor",
    ]
    relation_to_client: Literal[
        "own", "supplier", "ecosystem", "competitor", "regulator", "topic_of_offering"
    ]
    importance: Priority = 2
    linked_offering_ids: list[str] = []
    meta: ClaimMeta


class WatchTopic(DomainModel):
    query: str
    language: str
    linked_offering_ids: list[str] = []
    linked_value_prop_ids: list[str] = []
    event_types: list[EventType] = []


class SeasonalWindow(DomainModel):
    label: str
    months: list[Annotated[int, Field(ge=1, le=12)]]
    geo: str | None = None
    query_terms: list[str] = []
    meta: ClaimMeta


class SourceHint(DomainModel):
    kind: Literal["official_blog", "regulator", "journal", "news_topic", "press_release", "changelog"]
    domain: str | None = None
    reason: str


class RefreshPolicy(DomainModel):
    news_scan_interval_hours: int = Field(ge=0)
    refresh_check_interval_days: int = Field(ge=1)
    description_refresh_eligible: bool
    min_signal_strength: UnitInterval


class SiteChangeStats(DomainModel):
    dated_pages: int = 0
    posts_last_90d: int = 0
    median_days_between_posts: float | None = None
    last_content_update: date | None = None
    has_news_or_changelog_section: bool = False


class FreshnessProfile(DomainModel):
    sensitivity: FreshnessSensitivity
    sensitivity_score: UnitInterval
    score_components: dict[str, float]
    rationale: str
    drivers: list[FreshnessDriver] = []
    watch_entities: list[WatchEntity] = []
    watch_topics: list[WatchTopic] = []
    event_types: list[EventType] = []
    seasonality: list[SeasonalWindow] = []
    suggested_sources: list[SourceHint] = []
    source_packs: list[str] = []
    regions: list[CountryCode] = []
    languages: list[str] = []
    excluded_topics: list[str] = []
    regulated_domain: bool = False
    domain_kind: Literal["healthcare", "finance", "legal", "none"] = "none"
    evergreen_expected: bool = True
    refresh_policy: RefreshPolicy
    site_change_stats: SiteChangeStats = SiteChangeStats()
    meta: ClaimMeta

    @property
    def operating_level(self) -> OperatingLevel:
        return OPERATING_LEVEL_BY_SENSITIVITY[self.sensitivity]


class CoverageReport(DomainModel):
    pages_discovered: int = 0
    pages_fetched: int = 0
    pages_used: int = 0
    page_types_found: dict[str, int] = {}
    missing: list[str] = []
    blocked: dict[str, int] = {}
    sources_used: dict[SourceKind, int] = {}
    languages: dict[str, int] = {}
    warnings: list[str] = []


class ProfileQuality(DomainModel):
    claims_total: int
    claims_verified: int
    claims_dropped_unverified: int
    injection_flags: int
    thin_site: bool
    label: Literal["rich", "adequate", "thin"]
    completeness: UnitInterval


class BusinessProfile(DomainModel):
    profile_id: UUID
    client_id: UUID
    version: int = Field(ge=1)
    parent_version: int | None = None
    crawl_run_id: UUID | None = None
    status: Literal["draft", "awaiting_confirmation", "confirmed", "superseded"]
    schema_version: Literal["1"] = "1"
    generated_at: AwareDatetime
    identity: BusinessIdentity
    offerings: list[Offering] = []
    value_propositions: list[ValueProposition] = []
    target_customers: list[StatedAudience] = []
    locations: LocationSummary = LocationSummary()
    conversion_actions: list[ConversionAction] = []
    brand_terms: list[BrandTerm] = []
    niche_keywords: list[NicheKeyword] = []
    buyer_question_ids: list[UUID] = []
    competitor_hints: list[CompetitorHint] = []
    content_pillars: list[ContentPillar] = []
    freshness: FreshnessProfile
    coverage: CoverageReport = CoverageReport()
    quality: ProfileQuality


class ProfileChange(DomainModel):
    id: str
    field: str
    before: str | None
    after: str | None


class ProfileDiff(DomainModel):
    from_version: int
    to_version: int
    added: list[str] = []
    removed: list[str] = []
    changed: list[ProfileChange] = []
    significance: Literal["high", "medium", "low"]


class OnboardingInput(DomainModel):
    client_id: UUID
    website_url: str | None = None
    channel_ref: str | None = None
    business_name: str | None = None
    country_hint: CountryCode | None = None
    ownership_attested: bool = False
    scope_mode: Literal["domain", "subpath", "subdomain"] = "domain"
    crawl_profile: Literal["client", "competitor"] = "client"

    @model_validator(mode="after")
    def _needs_one_identifier(self) -> "OnboardingInput":
        if not (self.website_url or self.channel_ref or self.business_name):
            raise ValueError("provide a website, a channel or a business name")
        return self
