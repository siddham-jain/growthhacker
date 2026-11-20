from datetime import timedelta
from typing import Any

from app.domain.analytics import VALUELESS_AGE_METHODS
from app.domain.card_copy import CardCopy
from app.domain.common import ChannelRole
from app.domain.confidence import ConfidenceLabel
from app.domain.recommendations import (
    CreatorSubject,
    PatternSpec,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    VideoIdeaSubject,
)
from app.services.recommend.confidence import rule_for
from app.services.recommend.evidence.traits import slot_template
from app.services.recommend.gates.base import ALL_TYPES, Gate, GateEnv, GateOutcome, Phase, register_gate
from app.services.recommend.reasons import make_reason

PATTERN_TYPES = frozenset({RecType.title_pattern, RecType.thumbnail_pattern, RecType.format})


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


@register_gate
class UserFeedbackGate(Gate):
    name = "user_feedback"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 5

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        status = env.ctx.feedback.hidden_keys.get(draft.stable_key)
        if status is not None:
            return self.suppressed(draft, "hidden_by_feedback", status=status)
        return self.passed(draft)


@register_gate
class PolicyGuardrailsStructural(Gate):
    """no revenue, monetisation, demographic, audience-geo or category field may ride along on a draft."""

    name = "policy_guardrails_structural"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 10

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        dumped = draft.model_dump(mode="json", include={"subject", "reasons", "fit_reasons", "lenses"})
        terms = env.ctx.settings.forbidden_field_terms
        hit = next((key for key in sorted(_keys(dumped)) if any(term in key for term in terms)), None)
        if hit is not None:
            return self.suppressed(draft, "policy_field", field=hit)
        return self.passed(draft)


def reference_complete(ref: ReferenceVideo, draft: RecommendationDraft) -> bool:
    age = ref.views_at_age
    if not (ref.url and ref.channel and ref.published_at and ref.outlier_ratio is not None):
        return False
    if age.views is None or age.method in VALUELESS_AGE_METHODS:
        return False
    if ref.published_at > draft.as_of:
        return False
    in_window = ref.published_at >= draft.as_of - timedelta(days=draft.window_days)
    # the client's own video on a refresh card may predate the window; every peer reference may not
    return in_window or ref.channel_role is ChannelRole.own


@register_gate
class EvidenceCompleteness(Gate):
    name = "evidence_completeness"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 20

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        complete = [ref for ref in draft.reference_pool if reference_complete(ref, draft)]
        if len(complete) == len(draft.reference_pool):
            return self.passed(draft)
        if not complete:
            return self.suppressed(draft, "no_complete_refs", dropped=len(draft.reference_pool))
        repaired = draft.model_copy(update={"reference_pool": complete})
        repaired = env.reannotate(repaired, env.references.select(repaired))
        return self.repaired(
            repaired, "incomplete_refs_dropped", dropped=len(draft.reference_pool) - len(complete)
        )


@register_gate
class MinReferenceVideos(Gate):
    name = "min_reference_videos"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 30

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        policy = env.ctx.settings.gates
        refs = draft.references
        minimum = policy.min_refs(draft.type.value)
        if len(refs) < minimum:
            return self.suppressed(draft, "too_few_refs", found=len(refs), required=minimum)
        channels = len({ref.channel_id for ref in refs})
        required = policy.min_channels(draft.type.value)
        if channels < required:
            code = "single_channel_pattern" if draft.type in PATTERN_TYPES else "too_few_channels"
            return self.suppressed(draft, code, found=channels, required=required)
        return self.passed(draft)


@register_gate
class FitToClient(Gate):
    name = "fit_to_client"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 40
    min_topic_refs = 2

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        ctx = env.ctx
        if not draft.fit_reasons:
            return self.suppressed(draft, "no_fit", detail="no_fit_reason")
        if isinstance(draft.subject, CreatorSubject):
            share = draft.subject.relevance.match_share
            if share < ctx.settings.creators.min_match_share:
                return self.suppressed(draft, "no_fit", match_share=share)
            return self.passed(draft)
        if draft.type is RecType.description_refresh:
            return self.passed(draft)
        matching = sum(
            1 for ref in draft.references if any(topic in ctx.catalog for topic in ref.matched_topic_ids)
        )
        if matching < self.min_topic_refs:
            return self.suppressed(draft, "no_fit", topic_matching_refs=matching)
        return self.passed(draft)


@register_gate
class NoRepeatClient(Gate):
    """don't suggest what the client just made; judge a fresh upload at day 7 before suggesting more."""

    name = "no_repeat_client"
    phase = Phase.pre_writer
    applies_to = frozenset({RecType.video_idea, RecType.news_opportunity})
    order = 50

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        ctx = env.ctx
        settings = ctx.settings
        topics = set(draft.topic_ids)
        own = [video for video in ctx.index.own_videos if video.topic_ids & topics]
        if not own:
            return self.passed(draft)
        newest = own[0]
        if newest.age_days < settings.pending_judgement_days:
            return self.suppressed(
                draft, "client_recent_pending", published=newest.published_at.date().isoformat()
            )
        format_key = draft.subject.format_key if isinstance(draft.subject, VideoIdeaSubject) else None
        guard_days = max(draft.window_days, settings.repeat_guard_days)
        repeat = next(
            (
                video
                for video in own
                if video.age_days <= guard_days and (format_key is None or video.format_key == format_key)
            ),
            None,
        )
        if repeat is not None:
            return self.suppressed(
                draft, "client_already_published", published=repeat.published_at.date().isoformat()
            )
        if len(draft.fit_reasons) >= settings.max_fit_reasons:
            return self.passed(draft)
        reason = make_reason(
            f"f{len(draft.fit_reasons) + 1}",
            "CLIENT_COVERED_DIFFERENT_FORMAT",
            {"date": newest.published_at.date()},
        )
        annotated = draft.model_copy(
            update={
                "fit_reasons": [*draft.fit_reasons, reason],
                "allowed_dates": sorted({*draft.allowed_dates, newest.published_at.date()}),
            }
        )
        return self.repaired(annotated, "covered_in_other_format")


@register_gate
class NoCopyingStructural(Gate):
    name = "no_copying_structural"
    phase = Phase.pre_writer
    applies_to = frozenset({RecType.video_idea, *PATTERN_TYPES})
    order = 60

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject):
            text = subject.topic_text
        else:
            assert isinstance(subject, PatternSpec)
            text = slot_template(subject.traits)
        hit = env.copy.check(text)
        if hit is not None:
            return self.suppressed(
                draft, "copy_risk", rule=hit.rule, score=round(hit.score, 1), title=hit.title
            )
        return self.passed(draft)


@register_gate
class ContradictoryEvidence(Gate):
    name = "contradictory_evidence"
    phase = Phase.pre_writer
    applies_to = PATTERN_TYPES
    order = 65

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        if "contradictory_large_samples" in draft.flags:
            return self.suppressed(draft, "contradictory_evidence")
        return self.passed(draft)


@register_gate
class HonestConfidence(Gate):
    """recomputes the label with the type's rule; a mismatch is corrected downwards, never upwards."""

    name = "honest_confidence"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 70

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        rule = rule_for(draft.type)
        if rule is None:
            if draft.confidence is None:
                return self.suppressed(draft, "missing_confidence")
            return self.passed(draft)
        expected = rule.label(draft, env.ctx)
        if draft.confidence == expected:
            return self.passed(draft)
        corrected = draft.model_copy(update={"confidence": expected})
        return self.downgraded(corrected, "confidence_recomputed", label=expected.label.value)


@register_gate
class ThinRange(Gate):
    name = "thin_range"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 80

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        confidence = draft.confidence
        insufficient = draft.expected_range is not None and draft.expected_range.basis == "insufficient"
        if not insufficient or confidence is None or confidence.label is ConfidenceLabel.low:
            return self.passed(draft)
        lowered = confidence.model_copy(
            update={
                "label": ConfidenceLabel.low,
                "caps_applied": [*confidence.caps_applied, "range_insufficient"],
            }
        )
        return self.downgraded(draft.model_copy(update={"confidence": lowered}), "range_insufficient")


@register_gate
class HasLens(Gate):
    name = "has_lens"
    phase = Phase.pre_writer
    applies_to = ALL_TYPES
    order = 85

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome:
        if not draft.lenses:
            return self.suppressed(draft, "no_lens")
        return self.passed(draft)
