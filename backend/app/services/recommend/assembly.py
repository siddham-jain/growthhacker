from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.card_copy import CardCopy
from app.domain.recommendations import (
    NewsExtras,
    NewsSubject,
    Recommendation,
    RecommendationDraft,
    RecommendationExtras,
    RecType,
    RefreshExtras,
    RefreshSubject,
    RelatedRecommendation,
    recommendation_id,
)
from app.domain.refresh import DescriptionRefresh
from app.services.recommend.context import RecommendContext
from app.services.recommend.fingerprint import evidence_hash

MIN_EXPIRY_DAYS = 3
EXAMPLE_TITLE_TYPES = frozenset({RecType.video_idea, RecType.title_pattern})


@dataclass(frozen=True, slots=True)
class CardMeta:
    # persisted beside the card so tomorrow's run knows whether it is new, ongoing or updated
    rendered_by: str
    attempts: int
    evidence_hash: str
    first_seen_at: datetime
    unchanged_since: datetime


@dataclass(frozen=True, slots=True)
class AssembledCard:
    recommendation: Recommendation
    draft: RecommendationDraft
    copy: CardCopy
    meta: CardMeta


class ContractAssembler:
    """code fills every contract field except the words; the writer never touches ids, numbers or references."""

    def assemble(
        self,
        draft: RecommendationDraft,
        copy: CardCopy,
        ctx: RecommendContext,
        *,
        run_id: UUID,
        generated_at: datetime,
        related: list[RelatedRecommendation],
        refresh: DescriptionRefresh | None = None,
    ) -> AssembledCard:
        digest = evidence_hash(draft)
        previous = ctx.previous_cards.get(draft.stable_key)
        if previous is None:
            status, first_seen, unchanged = "new", generated_at, generated_at
        elif previous.evidence_hash == digest:
            status, first_seen, unchanged = "ongoing", previous.first_seen_at, previous.unchanged_since
        else:
            status, first_seen, unchanged = "updated", previous.first_seen_at, generated_at
        question = ctx.catalog.for_question(draft.buyer_question_id)
        related_questions = [
            topic.label
            for question_id in draft.related_buyer_question_ids
            if (topic := ctx.catalog.for_question(question_id)) is not None
        ]
        referenced = [reason_id for ids in copy.reason_fact_ids for reason_id in ids]
        why_structured = [reason for reason in draft.reasons if reason.reason_id in referenced]
        fit_structured = [reason for reason in draft.fit_reasons if reason.reason_id in copy.fit_fact_ids]
        rendered_by = "template" if copy.copy_source == "template" else "llm"
        expires_at = generated_at + timedelta(days=max(MIN_EXPIRY_DAYS, draft.window_days // 3))
        extras = RecommendationExtras(
            stable_key=draft.stable_key,
            run_id=run_id,
            rendered_by=rendered_by,
            status=status,
            first_seen_at=first_seen,
            expires_at=expires_at,
            buyer_question_id=draft.buyer_question_id,
            lens_reasons=draft.lenses,
            why_it_works_structured=why_structured,
            fit_structured=fit_structured,
            expected_range=draft.expected_range,
            gap=draft.gap,
            subject=draft.subject,
            related_buyer_questions=related_questions,
            example_titles=[copy.working_title]
            if copy.working_title and draft.type in EXAMPLE_TITLE_TYPES
            else [],
            related=related,
            flags=sorted(set(draft.flags)),
            news=self._news(draft),
            refresh=self._refresh(draft, refresh),
        )
        range_ = draft.expected_range
        recommendation = Recommendation(
            id=recommendation_id(draft.stable_key),
            type=draft.type,
            summary=copy.summary,
            lens=[assignment.lens for assignment in draft.lenses],
            buyer_question=question.label if question else None,
            why_it_works=list(copy.why_it_works),
            reference_videos=draft.references,
            fit_to_client=copy.fit_to_client,
            expected_range_vs_baseline=range_.display
            if range_ and range_.basis != "not_applicable"
            else None,
            confidence=draft.confidence,
            window_days=draft.window_days,
            generated_at=generated_at,
            extras=extras,
        )
        meta = CardMeta(
            rendered_by=rendered_by,
            attempts=copy.attempts,
            evidence_hash=digest,
            first_seen_at=first_seen,
            unchanged_since=unchanged,
        )
        return AssembledCard(recommendation=recommendation, draft=draft, copy=copy, meta=meta)

    @staticmethod
    def _news(draft: RecommendationDraft) -> NewsExtras | None:
        subject = draft.subject
        if not isinstance(subject, NewsSubject):
            return None
        return NewsExtras(
            reference_sources=subject.reference_sources,
            urgency=subject.urgency,
            format_suggestion=subject.format_suggestion,
            compliance=subject.compliance,
        )

    @staticmethod
    def _refresh(draft: RecommendationDraft, refresh: DescriptionRefresh | None) -> RefreshExtras | None:
        subject = draft.subject
        if not isinstance(subject, RefreshSubject):
            return None
        assert refresh is not None, "refresh cards need 09's DescriptionRefresh for byte counts"
        return RefreshExtras(
            operations=subject.operations,
            advisories=subject.advisories,
            review_required=subject.review_required,
            inserted_bytes=refresh.inserted_bytes,
            resulting_bytes=refresh.resulting_bytes,
        )
