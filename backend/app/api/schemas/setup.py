from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas.clients import ChannelRef
from app.api.schemas.common import ApiModel, WindowDays
from app.api.schemas.runs import RunOut
from app.domain.common import SubscriberBand
from app.domain.competitors import (
    CandidateRole,
    CandidateStatus,
    ChannelEvidence,
    CompetitorRelation,
    OfferingMatch,
    TopicUploadMatch,
)
from app.domain.profile import BusinessProfile, BuyerQuestionSourceKind, FunnelStage
from app.domain.web import SourceKind
from app.domain.youtube import OwnershipVerdict

# 99 C39/C40: the api uses the owners' review statuses, not suggested/active
TopicRefStatus = Literal["proposed", "confirmed", "rejected", "retired"]


class ChannelResolveIn(ApiModel):
    channel_ref: str = Field(min_length=2, max_length=300)


class ChannelCandidate(BaseModel):
    channel: ChannelRef
    ownership_evidence: list[str]
    confidence: Literal["high", "medium", "low"]


class ChannelResolveOut(BaseModel):
    candidates: list[ChannelCandidate]


class ProfileSource(BaseModel):
    # one row per evidence url behind the profile's claims, labelled with its acquisition tier (03 §14)
    url: str
    source_kind: SourceKind
    fetched_at: AwareDatetime | None = None
    snapshot_at: AwareDatetime | None = None
    claims: int = Field(ge=0)


class BusinessProfileView(BaseModel):
    profile: BusinessProfile
    overrides: dict[str, Any]
    effective: BusinessProfile
    version: int
    completeness: float = Field(ge=0, le=1)
    partial: bool
    sources: list[ProfileSource] = []


class KeywordItem(BaseModel):
    # mirrors 03's NicheKeyword; rejected keywords come from the tombstones that keep them out
    id: str
    text: str = Field(min_length=1, max_length=80)
    status: TopicRefStatus
    source: str
    language: str | None = None
    kind: str | None = None
    weight: float | None = Field(default=None, ge=0, le=1)
    updated_at: AwareDatetime | None = None


class BuyerQuestionItem(BaseModel):
    id: str
    text: str = Field(min_length=5, max_length=200)
    status: TopicRefStatus
    source: BuyerQuestionSourceKind
    funnel_stage: FunnelStage
    intent: str
    cited_video_ids: list[str] = []
    priority: int = Field(ge=1, le=3)
    created_at: AwareDatetime


# items the user adds are confirmed and locked at once (03 §5.14)
class KeywordCreate(ApiModel):
    text: str = Field(min_length=1, max_length=80)
    language: str = Field(default="en", min_length=2, max_length=12)
    weight: float = Field(default=1.0, ge=0, le=1)


class BuyerQuestionCreate(ApiModel):
    text: str = Field(min_length=5, max_length=200)
    language: str = Field(default="en", min_length=2, max_length=12)
    funnel_stage: FunnelStage | None = None
    priority: int = Field(default=2, ge=1, le=3)


class TopicRefPatch(ApiModel):
    # priority applies to buyer questions, weight to keywords (03 keeps them on different objects)
    text: str | None = Field(default=None, min_length=1, max_length=200)
    status: TopicRefStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    weight: float | None = Field(default=None, ge=0, le=1)


class BulkStatusIn(ApiModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: Literal["confirmed", "rejected"]


class CompetitorEvidence(BaseModel):
    kind: str
    url: str | None = None
    text: str


class ChannelRelevanceView(BaseModel):
    # raw facts only; the overlap and activity scores order candidates internally and never leave the api
    matching_uploads: int | None = None
    uploads_checked: int | None = None
    matching_uploads_label: str | None = None
    matched_topic_refs: list[TopicUploadMatch] = []
    size_band: Literal["peer", "larger", "much_larger", "smaller", "much_smaller", "unknown"]
    subscriber_band: SubscriberBand


class BusinessFactsView(BaseModel):
    # web-derived facts and the bucketed tier; the weighted overlap numbers stay internal
    tier: Literal["high", "medium", "low"]
    matched_offerings: list[OfferingMatch] = []
    matched_offerings_count: int
    geo_basis: str


class CompetitorChannelView(BaseModel):
    # raw public stats and the ownership verdict for one linked channel; no score or rank (99 C4)
    channel: ChannelRef
    is_primary: bool
    tracked: bool
    ownership: OwnershipVerdict
    evidence: ChannelEvidence
    relevance: ChannelRelevanceView
    preview_video_ids: list[str] = []
    flags: list[str] = []


class CompetitorView(BaseModel):
    id: str
    status: CandidateStatus
    role: CandidateRole
    relation: CompetitorRelation
    name: str
    official_domain: str | None = None
    confidence: Literal["high", "medium", "low"]
    channel: ChannelRef | None
    channels: list[CompetitorChannelView] = []
    sources: list[str] = []
    evidence: list[CompetitorEvidence] = []
    evidence_summary: str = ""
    business: BusinessFactsView
    flags: list[str] = []
    reject_reason: str | None = None
    added_by: Literal["system", "user"]
    decided_by: Literal["user", "auto", "system"] | None = None


class CompetitorAddIn(ApiModel):
    channel_ref: str = Field(min_length=2, max_length=300)
    role: Literal["competitor_set", "niche_set", "benchmark_set", "creator_seed"] = "competitor_set"


class CompetitorRejectIn(ApiModel):
    reason: Literal["not_competitor", "wrong_channel", "too_big", "too_small", "other"] | None = None
    note: str | None = Field(default=None, max_length=500)


class CompetitorRolePatch(ApiModel):
    role: Literal["competitor_set", "niche_set", "benchmark_set", "creator_seed"]


class ChannelAttachIn(ApiModel):
    # null means "none of these": continue website-only
    channel_id: str | None = Field(default=None, pattern=r"^UC[0-9A-Za-z_-]{22}$")


class ConfirmSetupIn(ApiModel):
    window_days: WindowDays | None = None


class SetupCounts(BaseModel):
    """confirmed inputs at request time; the frozen set is validated in the worker and reported on the run."""

    competitors_confirmed: int
    keywords_active: int
    buyer_questions_active: int


class ConfirmSetupOut(BaseModel):
    run: RunOut
    validation: SetupCounts
