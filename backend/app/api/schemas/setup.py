from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas.clients import ChannelRef
from app.api.schemas.common import ApiModel, WindowDays
from app.api.schemas.runs import RunOut
from app.domain.profile import BusinessProfile

TopicRefStatus = Literal["suggested", "active", "rejected"]


class ChannelResolveIn(ApiModel):
    channel_ref: str = Field(min_length=2, max_length=300)


class ChannelCandidate(BaseModel):
    channel: ChannelRef
    ownership_evidence: list[str]
    confidence: Literal["high", "medium", "low"]


class ChannelResolveOut(BaseModel):
    candidates: list[ChannelCandidate]


class ProfileSource(BaseModel):
    url: str
    title: str | None = None
    fetched_at: AwareDatetime | None = None


class BusinessProfileView(BaseModel):
    profile: BusinessProfile
    overrides: dict[str, Any]
    effective: BusinessProfile
    version: int
    completeness: float = Field(ge=0, le=1)
    partial: bool
    sources: list[ProfileSource] = []


class KeywordItem(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=80)
    status: TopicRefStatus
    source: Literal["profile", "user", "spotlit"]
    priority: int = Field(ge=1, le=5)
    created_at: AwareDatetime


class BuyerQuestionItem(BaseModel):
    id: str
    text: str = Field(min_length=5, max_length=200)
    status: TopicRefStatus
    source: Literal["profile", "user", "spotlit_ai_tracking"]
    intent_stage: Literal["awareness", "consideration", "decision"] | None = None
    cited_video_ids: list[str] = []
    priority: int = Field(ge=1, le=5)
    created_at: AwareDatetime


class KeywordCreate(ApiModel):
    text: str = Field(min_length=1, max_length=80)
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["suggested", "active"] = "active"


class BuyerQuestionCreate(ApiModel):
    text: str = Field(min_length=5, max_length=200)
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["suggested", "active"] = "active"


class TopicRefPatch(ApiModel):
    text: str | None = Field(default=None, min_length=1, max_length=200)
    status: TopicRefStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)


class BulkStatusIn(ApiModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: Literal["active", "rejected"]


class CompetitorEvidence(BaseModel):
    kind: str
    url: str | None = None
    text: str


class CompetitorView(BaseModel):
    # mirrors 04's candidate; no score or rank (policy, 99 C4)
    id: str
    status: Literal["suggested", "confirmed", "rejected"]
    channel: ChannelRef | None
    name: str
    source: str
    evidence: list[CompetitorEvidence] = []
    reject_reason: str | None = None
    added_by: Literal["system", "user"]


class CompetitorAddIn(ApiModel):
    channel_ref: str = Field(min_length=2, max_length=300)
    name: str | None = Field(default=None, max_length=120)


class CompetitorRejectIn(ApiModel):
    reason: Literal["not_competitor", "wrong_channel", "too_big", "too_small", "other"] | None = None
    note: str | None = Field(default=None, max_length=500)


class ConfirmSetupIn(ApiModel):
    window_days: WindowDays | None = None


class SetupCounts(BaseModel):
    competitors_confirmed: int
    keywords_active: int
    buyer_questions_active: int


class ConfirmSetupOut(BaseModel):
    run: RunOut
    validation: SetupCounts
