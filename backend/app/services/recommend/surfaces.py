from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime

from app.domain.common import DomainModel, VideoId, WindowDays
from app.domain.confidence import Confidence, ConfidenceBasis
from app.domain.news import NewsSourceRef
from app.domain.recommendations import (
    NewsSubject,
    RecommendationDraft,
    RecommendationId,
    RecType,
    ReferenceVideo,
    RefreshSubject,
    recommendation_id,
)
from app.domain.refresh import Advisory, InsertionOp
from app.services.recommend.gates.structural import reference_complete


class EarlySignal(DomainModel):
    # first-mover news with fewer than 3 references; its own lane, never a recommendation card
    id: RecommendationId
    news_signal_id: UUID
    key_entity: str
    angle: str
    urgency: str
    expires_at: AwareDatetime
    sources: list[NewsSourceRef]
    reference_videos: list[ReferenceVideo] = []
    confidence: Confidence
    buyer_question_id: UUID | None = None
    window_days: WindowDays
    statements: list[str] = []


class HygieneItem(DomainModel):
    # rule-based description fixes (09 fallback); its own list, outside the recommendation cards
    id: RecommendationId
    video_id: VideoId
    operations: list[InsertionOp]
    advisories: list[Advisory] = []
    review_required: bool
    confidence: Confidence | None = None
    statements: list[str] = []


@dataclass(slots=True)
class RoutedDrafts:
    main: list[RecommendationDraft] = field(default_factory=list)
    early: list[RecommendationDraft] = field(default_factory=list)
    hygiene: list[RecommendationDraft] = field(default_factory=list)
    refresh: list[RecommendationDraft] = field(default_factory=list)


def route(drafts: list[RecommendationDraft], min_refs: dict[RecType, int]) -> RoutedDrafts:
    """cards with fewer than the minimum references leave the list for their own surfaces (owner decision o3)."""
    routed = RoutedDrafts()
    for draft in drafts:
        enough = len(draft.reference_pool) >= min_refs[draft.type]
        if draft.type is RecType.news_opportunity:
            first_mover = (
                draft.confidence is not None and draft.confidence.basis is ConfidenceBasis.first_mover
            )
            (routed.main if enough and not first_mover else routed.early).append(draft)
        elif draft.type is RecType.description_refresh:
            rule_based = draft.confidence is not None and draft.confidence.basis is ConfidenceBasis.rule_based
            (routed.refresh if enough and not rule_based else routed.hygiene).append(draft)
        else:
            routed.main.append(draft)
    return routed


def early_signal(draft: RecommendationDraft, as_of: datetime, min_sources: int) -> EarlySignal | str:
    """returns the signal, or the reason it cannot be shown."""
    subject = draft.subject
    assert isinstance(subject, NewsSubject)
    if subject.expires_at <= as_of:
        return "expired"
    publishers = {source.publisher.strip().lower() for source in subject.reference_sources}
    if len(publishers) < min_sources:
        return "early_signal_uncorroborated"
    confidence = draft.confidence
    if confidence is None or confidence.basis is not ConfidenceBasis.first_mover:
        return "early_signal_not_first_mover"
    return EarlySignal(
        id=recommendation_id(draft.stable_key),
        news_signal_id=subject.news_signal_id,
        key_entity=subject.key_entity,
        angle=subject.angle,
        urgency=subject.urgency,
        expires_at=subject.expires_at,
        sources=subject.reference_sources,
        reference_videos=[ref for ref in draft.reference_pool if reference_complete(ref, draft)],
        confidence=confidence,
        buyer_question_id=draft.buyer_question_id,
        window_days=draft.window_days,
        statements=[reason.statement for reason in draft.reasons],
    )


def hygiene_item(draft: RecommendationDraft) -> HygieneItem:
    subject = draft.subject
    assert isinstance(subject, RefreshSubject)
    return HygieneItem(
        id=recommendation_id(draft.stable_key),
        video_id=subject.video_id,
        operations=subject.operations,
        advisories=subject.advisories,
        review_required=subject.review_required,
        confidence=draft.confidence,
        statements=[reason.statement for reason in draft.reasons],
    )
