from datetime import date
from typing import Literal

from pydantic import Field

from app.domain.analytics import ViewsAtAgeSummary
from app.domain.common import DomainModel, VideoId, WindowDays
from app.domain.confidence import ConfidenceLabel
from app.domain.recommendations import (
    FIT_TO_CLIENT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    WHY_IT_WORKS_MAX_ITEMS,
    Lens,
    Reason,
    RecType,
    WhyItWorksItem,
)

MAX_PACK_FACTS = 12
MAX_PACK_VIDEOS = 10


class PackFact(DomainModel):
    alias: str
    supports: list[Literal["why", "fit"]]
    reason: Reason


class PackVideo(DomainModel):
    # channel_name and title are untrusted public text and must never be copied
    alias: str
    video_id: VideoId
    channel_name: str
    title: str
    published_at: date
    views_at_age: ViewsAtAgeSummary
    views_day7: int | None
    outlier_ratio: float | None


class PackAlias(DomainModel):
    alias: str
    text: str


class PackClient(DomainModel):
    name: str
    offerings: list[PackAlias] = []
    value_props: list[PackAlias] = []
    buyer_questions: list[PackAlias] = []
    locations: list[str] = []
    vertical: str


class PackTopic(DomainModel):
    label: str
    buyer_question_alias: str | None = None


class PackConfidence(DomainModel):
    label: ConfidenceLabel
    outliers: int
    channels: int


class EvidencePack(DomainModel):
    # the only facts, numbers, videos and names the writer may use
    draft_id: str
    rec_type: RecType
    lens: list[Lens]
    window_days: WindowDays
    language: str
    client: PackClient
    topic: PackTopic
    facts: list[PackFact] = Field(max_length=MAX_PACK_FACTS)
    videos: list[PackVideo] = Field(max_length=MAX_PACK_VIDEOS)
    entities: list[str] = []
    confidence: PackConfidence
    expected_range_vs_baseline: str | None
    forbidden_titles: list[str] = []
    allowed_dates: list[date] = []


class Violation(DomainModel):
    rule: str
    field: str
    token: str | None = None
    detail: str


class CritiqueScores(DomainModel):
    specific: int = Field(ge=1, le=5)
    evidenced: int = Field(ge=1, le=5)
    relevant: int = Field(ge=1, le=5)
    actionable: int = Field(ge=1, le=5)
    not_obvious: int = Field(ge=1, le=5)


class FieldCheck(DomainModel):
    field: str
    ok: bool


class CritiqueIssue(DomainModel):
    field: str
    problem: str
    fix_hint: str


class CardCritique(DomainModel):
    scores: CritiqueScores
    faithful: list[FieldCheck] = []
    generic_phrases: list[str] = []
    issues: list[CritiqueIssue] = []
    verdict: Literal["pass", "revise", "reject_copy"]


class CardCopy(DomainModel):
    draft_id: str
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS)
    working_title: str | None = None
    why_it_works: list[WhyItWorksItem] = Field(min_length=1, max_length=WHY_IT_WORKS_MAX_ITEMS)
    fit_to_client: str = Field(min_length=1, max_length=FIT_TO_CLIENT_MAX_CHARS)
    reason_fact_ids: list[list[str]]
    fit_fact_ids: list[str]
    copy_source: Literal["llm", "llm_revised", "template"]
    degrade_reason: (
        Literal["llm_unavailable", "budget", "deadline", "validation_exhausted", "refusal"] | None
    ) = None
    prompt_id: str
    prompt_version: str
    model: str | None = None
    attempts: int = Field(ge=0)
    violations_last: list[Violation] = []
    critique: CardCritique | None = None
    language: str
