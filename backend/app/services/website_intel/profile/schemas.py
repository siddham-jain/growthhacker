from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.news import EventType
from app.domain.profile import BuyerQuestionIntent, FactKind, FunnelStage


class LlmModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ExpectedInfo = Literal[
    "offerings", "pricing", "conversion", "locations", "proof", "faq", "about", "news_cadence", "audience"
]


class PagePick(LlmModel):
    id: str
    expected_info: list[ExpectedInfo] = []
    reason: str = Field(default="", max_length=120)


class PageSelectionOutput(LlmModel):
    picks: list[PagePick]


class FactAttr(LlmModel):
    key: str
    value: str


class LlmFact(LlmModel):
    kind: FactKind
    text: str
    quote: str
    attrs: list[FactAttr] = []


class LlmPageFacts(LlmModel):
    page_id: str
    summary: str = ""
    facts: list[LlmFact] = []


class PageFactsOutput(LlmModel):
    pages: list[LlmPageFacts]


OfferingKind = Literal[
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
ValuePropKind = Literal[
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
AudienceKind = Literal[
    "business_role", "business_type", "condition_or_need", "use_case", "geography", "other"
]
LocationKind = Literal["hospital", "clinic", "branch", "store", "office", "service_area", "virtual", "other"]
BusinessModelValue = Literal[
    "local_service",
    "multi_location_service",
    "b2b_saas",
    "b2c_app",
    "ecommerce",
    "marketplace",
    "media_publisher",
    "manufacturer",
    "agency",
    "nonprofit",
    "other",
]
GeoScope = Literal["local", "regional", "national", "multinational", "global_online"]
BrandKind = Literal["company", "product", "sub_brand", "program", "slogan", "trademark"]
HintType = Literal["named_on_site", "comparison_page", "alternative_mention", "category_peer_inferred"]


# enum-like fields are strings: full enums overflow the structured-output grammar; the binder coerces them
class LlmOffering(LlmModel):
    name: str
    kind: str
    description: str
    url: str
    parent_name: str
    is_primary: bool
    price_ids: list[str]
    evidence_ids: list[str]


class LlmValueProp(LlmModel):
    statement: str
    kind: str
    proof_points: list[str]
    offering_names: list[str]
    evidence_ids: list[str]


class LlmAudience(LlmModel):
    segment: str
    kind: str
    evidence_ids: list[str]


class LlmLocation(LlmModel):
    name: str
    kind: str
    address: str
    city: str
    region: str
    country: str
    evidence_ids: list[str]


class LlmConversion(LlmModel):
    cta_id: str
    offering_names: list[str]
    is_primary: bool


class LlmBrandTerm(LlmModel):
    term: str
    kind: str
    variants: list[str]
    evidence_ids: list[str]


class LlmCompetitorHint(LlmModel):
    name: str
    domain: str
    hint_type: str
    evidence_ids: list[str]
    basis_ids: list[str]


class CoreProfileOutput(LlmModel):
    name: str
    name_evidence_ids: list[str]
    legal_name: str
    one_liner: str
    one_liner_basis_ids: list[str]
    description: str
    industry: str
    industry_basis_ids: list[str]
    niche: str
    business_model: str
    geo_scope: str
    primary_language: str
    languages: list[str]
    country: str
    offerings: list[LlmOffering]
    conversions: list[LlmConversion]
    brand_terms: list[LlmBrandTerm]


class ClaimsOutput(LlmModel):
    value_propositions: list[LlmValueProp]
    audiences: list[LlmAudience]
    locations: list[LlmLocation]
    competitor_hints: list[LlmCompetitorHint]


KeywordKind = Literal[
    "offering",
    "problem",
    "procedure",
    "technology",
    "category",
    "location_modifier",
    "intent_modifier",
    "brand_adjacent",
]


class LlmSeedKeyword(LlmModel):
    term: str
    language: str = "en"
    kind: KeywordKind
    evidence_ids: list[str] = []
    inventory_slugs: list[str] = []
    offering_names: list[str] = []


class LlmExpandedKeyword(LlmModel):
    term: str
    language: str = "en"
    script: str = ""
    kind: KeywordKind
    basis_terms: list[str]
    offering_names: list[str] = []


class LlmQuestion(LlmModel):
    text: str
    language: str = "en"
    funnel_stage: FunnelStage
    intent: BuyerQuestionIntent
    offering_names: list[str] = []
    basis_ids: list[str] = []
    geo_modifier: str = ""


class LlmPillar(LlmModel):
    name: str
    description: str
    offering_names: list[str] = []
    keyword_terms: list[str] = []
    question_texts: list[str] = []


class EnrichmentOutput(LlmModel):
    seed_keywords: list[LlmSeedKeyword] = []
    expanded_keywords: list[LlmExpandedKeyword] = []
    questions: list[LlmQuestion] = []
    pillars: list[LlmPillar] = []


EntityKind = Literal[
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
Relation = Literal["own", "supplier", "ecosystem", "competitor", "regulator", "topic_of_offering"]
SourceHintKind = Literal["official_blog", "regulator", "journal", "news_topic", "press_release", "changelog"]


class LlmDriver(LlmModel):
    statement: str
    basis_ids: list[str]


class LlmWatchEntity(LlmModel):
    name: str
    aliases: list[str] = []
    kind: EntityKind
    relation_to_client: Relation
    importance: Literal[1, 2, 3] = 2
    offering_names: list[str] = []
    evidence_ids: list[str] = []
    basis_ids: list[str] = []


class LlmWatchTopic(LlmModel):
    query: str
    language: str = "en"
    offering_names: list[str] = []
    event_types: list[EventType] = []


class LlmSeason(LlmModel):
    label: str
    months: list[int]
    geo: str = ""
    query_terms: list[str] = []
    basis_ids: list[str]


class LlmSourceHint(LlmModel):
    kind: SourceHintKind
    domain: str = ""
    reason: str


class FreshnessOutput(LlmModel):
    llm_score: Literal[0, 1, 2, 3, 4]
    rationale: str
    drivers: list[LlmDriver] = []
    watch_entities: list[LlmWatchEntity] = []
    watch_topics: list[LlmWatchTopic] = []
    event_types: list[EventType] = []
    seasonality: list[LlmSeason] = []
    suggested_sources: list[LlmSourceHint] = []
    regulated_domain: bool = False
    domain_kind: Literal["healthcare", "finance", "legal", "none"] = "none"
    evergreen_expected: bool = True
    excluded_topics: list[str] = []
