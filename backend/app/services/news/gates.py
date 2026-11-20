from typing import Any, ClassVar

from app.core.config import NewsSettings
from app.core.registry import quality_gate_registry
from app.domain.news import SourceTier
from app.domain.recommendations import GateAction, GateResult, NewsSubject, RecommendationDraft, RecType
from app.services.news.opportunity import SURFACE_EARLY_SIGNALS, SURFACE_RECOMMENDATIONS, SURFACE_REVIEW
from app.services.news.tiers import TIER1

GATE_NAME = "NewsEvidenceGate"


def surface_of(draft: RecommendationDraft) -> str:
    for surface in (SURFACE_REVIEW, SURFACE_EARLY_SIGNALS, SURFACE_RECOMMENDATIONS):
        if surface in draft.flags:
            return surface
    return SURFACE_RECOMMENDATIONS


@quality_gate_registry.register(GATE_NAME)
class NewsEvidenceGate:
    """pre-writer gate for news_opportunity drafts; first-mover drafts never reach the recommendation list."""

    name: ClassVar[str] = GATE_NAME
    phase: ClassVar[str] = "pre_writer"
    applies_to: ClassVar[frozenset[RecType]] = frozenset({RecType.news_opportunity})
    order: ClassVar[int] = 35

    def __init__(self, settings: NewsSettings | None = None) -> None:
        self._settings = settings or NewsSettings()

    def _fail(self, code: str, **features: Any) -> GateResult:
        return GateResult(gate=self.name, action=GateAction.suppress, reason_code=code, features=features)

    def check(self, draft: RecommendationDraft, ctx: Any = None, written: Any = None) -> GateResult:
        subject = draft.subject
        if not isinstance(subject, NewsSubject):
            return self._fail("not_a_news_draft")
        sources = subject.reference_sources
        publishers = {source.publisher for source in sources}
        tiers = [source.tier for source in sources]
        tier2 = {s.publisher for s in sources if s.tier is SourceTier.tier2_established}
        health = "health" in draft.flags
        if health and SourceTier.tier1_authority not in tiers:
            return self._fail("health_needs_authority")
        if not (set(tiers) & TIER1 or len(tier2) >= 2):
            return self._fail("weak_sources", publishers=len(publishers))
        if not subject.key_entity or subject.key_entity not in draft.entities:
            return self._fail("ungrounded_entity")
        if not any(reason.code == "OFFERING_LINK" for reason in draft.fit_reasons):
            return self._fail("no_offering_link")
        if subject.conversion_action_id is None:
            return self._fail("no_conversion_action")
        if health and not subject.compliance.disclaimers:
            return self._fail("missing_disclaimers")
        if subject.expires_at <= draft.as_of:
            return self._fail("expired")
        for reference in draft.references:
            if reference.views_at_age is None or not reference.url:
                return self._fail("incomplete_reference", video_id=reference.video_id)
        surface = surface_of(draft)
        reference_count = len(draft.references)
        if reference_count < self._settings.min_reference_videos:
            if surface == SURFACE_RECOMMENDATIONS:
                return self._fail("too_few_refs", references=reference_count)
            if self._settings.strict_reference_gate:
                return self._fail("strict_reference_gate", references=reference_count)
            if len(publishers) < self._settings.early_signal_min_sources:
                return self._fail("early_signal_needs_sources", publishers=len(publishers))
        return GateResult(
            gate=self.name,
            action=GateAction.pass_,
            features={"surface": surface, "references": reference_count, "publishers": len(publishers)},
        )
