import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, Protocol
from uuid import UUID

from app.core.registry import Registry
from app.domain.refresh import Advisory, ClaimLevel, RefreshTrigger, TriggerEvidence
from app.services.refresh.context import RefreshContext
from app.services.refresh.demand import DemandEvidence
from app.services.refresh.eligibility import VideoEligibility, VideoState
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.text import normalise


class TriggerKind(StrEnum):
    news_entity = "news_entity"
    stale_year = "stale_year"
    buyer_question_gap = "buyer_question_gap"
    above_fold_keyword = "above_fold_keyword"
    missing_cta = "missing_cta"
    missing_contact = "missing_contact"
    missing_chapters = "missing_chapters"
    missing_hashtags = "missing_hashtags"
    missing_disclaimer = "missing_disclaimer"
    broken_link = "broken_link"


KEYWORD_TRIGGER_KINDS = frozenset(
    {
        TriggerKind.news_entity,
        TriggerKind.stale_year,
        TriggerKind.buyer_question_gap,
        TriggerKind.above_fold_keyword,
    }
)


@dataclass(frozen=True, slots=True)
class Finding:
    """one detected reason to touch a description, with what a template needs to phrase the fix."""

    kind: TriggerKind
    video_id: str
    dedupe_key: str
    strength: float
    demand: float
    urgency: float
    evidence: TriggerEvidence
    keyword: str | None = None
    aliases: tuple[str, ...] = ()
    claim_level: ClaimLevel | None = None
    params: Mapping[str, str] = field(default_factory=dict)
    advisories: tuple[Advisory, ...] = ()
    demand_evidence: DemandEvidence | None = None
    news_signal_id: UUID | None = None

    @property
    def keyword_trigger(self) -> bool:
        return self.kind in KEYWORD_TRIGGER_KINDS

    @property
    def proposes_text(self) -> bool:
        return self.claim_level is not None

    @property
    def trigger_id(self) -> str:
        digest = hashlib.sha1(f"{self.video_id}|{self.dedupe_key}".encode()).hexdigest()
        return f"trg_{digest[:12]}"

    def to_trigger(self) -> RefreshTrigger:
        return RefreshTrigger(
            id=self.trigger_id,
            kind=self.kind.value,
            video_id=self.video_id,
            keyword=self.keyword,
            strength=round(min(1.0, max(0.0, self.strength)), 4),
            demand=round(min(1.0, max(0.0, self.demand)), 4),
            urgency=round(min(1.0, max(0.0, self.urgency)), 4),
            evidence=self.evidence,
            dedupe_key=self.dedupe_key,
        )


def dedupe_key(kind: TriggerKind, value: str) -> str:
    return f"{kind.value}:{normalise(value)}"


class TriggerDetector(Protocol):
    kind: ClassVar[TriggerKind]

    def detect(
        self,
        state: VideoState,
        gate: VideoEligibility,
        evidence: VideoContentEvidence,
        ctx: RefreshContext,
    ) -> list[Finding]: ...


trigger_registry: Registry[TriggerDetector] = Registry("refresh_trigger_registry")


def register_detector[T: type](cls: T) -> T:
    trigger_registry.add(cls.kind.value, cls())
    return cls
