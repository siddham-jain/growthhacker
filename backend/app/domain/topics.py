from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints

from app.domain.common import DirectnessLevel, DomainModel, UnitInterval, VideoId
from app.domain.profile import FunnelStage

TopicRefId = Annotated[str, StringConstraints(pattern=r"^tr_[0-9a-f]{12}$")]


class TopicKind(StrEnum):
    keyword = "keyword"
    offering = "offering"
    buyer_question = "buyer_question"
    news_entity = "news_entity"


class Facet(StrEnum):
    cost = "cost"
    eligibility = "eligibility"
    procedure = "procedure"
    symptoms = "symptoms"
    causes = "causes"
    treatment_options = "treatment_options"
    recovery = "recovery"
    risks = "risks"
    comparison = "comparison"
    location = "location"
    timing = "timing"
    definition = "definition"
    booking = "booking"


class TopicAlias(DomainModel):
    text: str
    norm_key: str
    phonetic_key: str | None = None
    lang: str | None = None
    script: str
    kind: Literal[
        "canonical",
        "client_alias",
        "morph_variant",
        "abbreviation",
        "transliteration",
        "translation",
        "synonym",
        "spelling_variant",
    ]
    origin: Literal["client", "profile_extracted", "rule", "llm_proposed"]
    status: Literal["confirmed", "proposed", "rejected"]
    anchor_text: str = Field(min_length=1)
    match_mode: Literal["phrase", "token_exact_case", "phrase_fuzzy"]
    weight: UnitInterval
    ambiguous: bool = False


class TopicRef(DomainModel):
    # always anchored to the client's own keywords, offerings or buyer questions
    id: TopicRefId
    client_id: UUID
    profile_version: int
    kind: TopicKind
    source_item_id: str
    label: str
    definition: str | None = None
    aliases: list[TopicAlias] = []
    negative_contexts: list[str] = []
    parent_ids: list[TopicRefId] = []
    offering_ids: list[str] = []
    funnel_stage: FunnelStage | None = None
    facets: list[Facet] = []
    core_entity_terms: list[str] = []
    locality_terms: list[str] = []
    weight: float = 1.0
    status: Literal["active", "disabled"] = "active"
    expires_at: AwareDatetime | None = None
    fingerprint: str


class TopicUniverseStats(DomainModel):
    keywords: int
    offerings: int
    buyer_questions: int
    aliases_confirmed: int
    aliases_proposed: int


class TopicUniverse(DomainModel):
    client_id: UUID
    profile_version: int
    universe_hash: str
    engine_version: str
    embedder_model_ids: dict[str, str]
    refs: list[TopicRef]
    stats: TopicUniverseStats
    warnings: list[str] = []
    built_at: AwareDatetime


class RemovedSpan(DomainModel):
    field: str
    start: int
    end: int
    reason: str


class PreparedFields(DomainModel):
    title: str
    chapters: list[str] = []
    description_head: str = ""
    description_rest: str = ""
    tags: list[str] = []
    hashtags: list[str] = []


class PreparedVideoText(DomainModel):
    video_id: VideoId
    content_hash: str
    prepared_hash: str
    fields: PreparedFields
    removed_spans: list[RemovedSpan] = []
    script_profile: dict[str, float] = {}
    code_mixed: bool = False
    lang_hint: str | None = None
    quality_flags: list[str] = []


class MatchEvidence(DomainModel):
    method: Literal["lexical", "semantic", "llm", "implied", "override"]
    field: (
        Literal["title", "chapters", "description_head", "description_rest", "tags", "hashtags", "combined"]
        | None
    )
    span_text: str | None = None
    start: int | None = None
    end: int | None = None
    alias_text: str | None = None
    alias_kind: str | None = None
    alias_status: str | None = None
    match_quality: UnitInterval
    negated: bool = False
    subsumed_by_ref: TopicRefId | None = None
    detail: dict[str, Any] = {}


class ReviewReason(StrEnum):
    semantic_only = "semantic_only"
    llm_decided = "llm_decided"
    llm_quote_unverified = "llm_quote_unverified"
    negated_evidence = "negated_evidence"
    ambiguous_alias = "ambiguous_alias"
    tags_only = "tags_only"
    proposed_alias_only = "proposed_alias_only"
    low_text_quality = "low_text_quality"
    degraded_mode = "degraded_mode"
    many_topics = "many_topics"
    method_conflict = "method_conflict"
    unadjudicated_budget = "unadjudicated_budget"
    unsupported_script = "unsupported_script"
    reference_video_sample = "reference_video_sample"
    qa_random_sample = "qa_random_sample"


class MatchMethod(StrEnum):
    lexical = "lexical"
    semantic = "semantic"
    lexical_semantic = "lexical+semantic"
    llm_adjudicated = "llm_adjudicated"
    implied = "implied"
    override = "override"


class TopicMatch(DomainModel):
    client_id: UUID
    video_id: VideoId
    topic_ref_id: TopicRefId
    topic_kind: TopicKind
    profile_version: int
    universe_hash: str
    prepared_hash: str
    engine_version: str
    decision: Literal["accepted", "rejected", "unadjudicated"]
    lexical_score: UnitInterval
    semantic_score: UnitInterval | None = None
    combined_score: UnitInterval
    method: MatchMethod
    confidence: Literal["high", "medium", "low"]
    is_primary: bool = False
    rank: int = 0
    implied_from: TopicRefId | None = None
    evidence: list[MatchEvidence] = []
    review_reasons: list[ReviewReason] = []
    degraded: list[Literal["no_llm", "no_embedder", "llm_budget"]] = []
    explanation: str
    matched_at: AwareDatetime

    @property
    def accepted(self) -> bool:
        return self.decision == "accepted"

    @property
    def review_flag(self) -> bool:
        return bool(self.review_reasons)

    @property
    def matched_terms(self) -> list[str]:
        return [item.alias_text for item in self.evidence if item.alias_text and not item.negated]


class DirectnessComponents(DomainModel):
    question_alignment: float
    facet_coverage: float
    entity_coverage: float
    specificity: float
    intent_cues: float
    penalties: float


class AnswerDirectness(DomainModel):
    # judged from metadata only; spoken content is never seen
    client_id: UUID
    video_id: VideoId
    buyer_question_ref_id: TopicRefId
    universe_hash: str
    prepared_hash: str
    score: UnitInterval
    level: DirectnessLevel
    components: DirectnessComponents
    facets_covered: list[Facet] = []
    facets_missing: list[Facet] = []
    method: Literal["deterministic", "deterministic+llm_tiebreak"]
    explanation: str
    basis: Literal["metadata_only"] = "metadata_only"


class AssignedTopic(DomainModel):
    topic_ref_id: TopicRefId
    kind: TopicKind
    confidence: Literal["high", "medium", "low"]
    combined_score: UnitInterval
    is_primary: bool


class VideoTopicAssignment(DomainModel):
    video_id: VideoId
    client_id: UUID
    universe_hash: str
    topics: list[AssignedTopic] = []
    primary_topic_id: TopicRefId | None = None
    unmatched_reason: (
        Literal[
            "no_topical_text", "below_threshold", "adjudicated_none", "unsupported_script", "unadjudicated"
        ]
        | None
    ) = None
