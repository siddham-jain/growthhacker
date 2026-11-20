from collections import Counter
from typing import ClassVar, Protocol

from app.core.registry import Registry
from app.domain.analytics import TRUE_AGE_METHODS
from app.domain.confidence import (
    HIGH_MIN_CHANNELS,
    HIGH_MIN_OUTLIERS,
    Confidence,
    ConfidenceLabel,
    ConfidenceRuleId,
)
from app.domain.recommendations import CreatorSubject, PatternSpec, RecommendationDraft, RecType
from app.services.recommend.context import RecommendContext

LABEL_RANK = {ConfidenceLabel.low: 0, ConfidenceLabel.medium: 1, ConfidenceLabel.high: 2}
RANK_LABEL = {rank: label for label, rank in LABEL_RANK.items()}
MEDIUM_MIN_OUTLIERS = 3
MEDIUM_MIN_CHANNELS = 2
PROXY_SHARE = 0.5


class ConfidenceRule(Protocol):
    rule_id: ClassVar[ConfidenceRuleId]
    applies_to: ClassVar[frozenset[RecType]]

    def label(self, draft: RecommendationDraft, ctx: RecommendContext) -> Confidence: ...


confidence_rule_registry: Registry[type[ConfidenceRule]] = Registry("confidence_rule_registry")


def register_confidence_rule[T: type](cls: T) -> T:
    confidence_rule_registry.add(cls.rule_id.value, cls)
    return cls


def rule_for(rec_type: RecType) -> ConfidenceRule | None:
    for key in sorted(confidence_rule_registry):
        rule = confidence_rule_registry.get(key)
        if rec_type in rule.applies_to:
            return rule()
    return None


def cap(label: ConfidenceLabel, maximum: ConfidenceLabel) -> ConfidenceLabel:
    return RANK_LABEL[min(LABEL_RANK[label], LABEL_RANK[maximum])]


def outlier_counts(draft: RecommendationDraft) -> tuple[int, int]:
    outliers = {ref.video_id: ref.channel_id for ref in draft.reference_pool if ref.is_outlier}
    return len(outliers), len(set(outliers.values()))


@register_confidence_rule
class DefaultConfidenceRule:
    """High = 5+ outliers across 3+ channels (and a lifted pattern); caps make it honest."""

    rule_id = ConfidenceRuleId.default_v1
    applies_to = frozenset(
        {RecType.video_idea, RecType.title_pattern, RecType.thumbnail_pattern, RecType.format}
    )

    def label(self, draft: RecommendationDraft, ctx: RecommendContext) -> Confidence:
        outliers, channels = outlier_counts(draft)
        pattern_lifted = not isinstance(draft.subject, PatternSpec) or draft.subject.ci_low > 1.0
        if outliers >= HIGH_MIN_OUTLIERS and channels >= HIGH_MIN_CHANNELS and pattern_lifted:
            label = ConfidenceLabel.high
        elif outliers >= MEDIUM_MIN_OUTLIERS and channels >= MEDIUM_MIN_CHANNELS:
            label = ConfidenceLabel.medium
        else:
            label = ConfidenceLabel.low
        caps: list[str] = []
        refs = draft.references or draft.reference_pool
        for name, maximum, applies in self._caps(draft, ctx, refs):
            if applies:
                capped = cap(label, maximum)
                if capped is not label:
                    caps.append(name)
                    label = capped
        if (
            refs
            and sum(1 for ref in refs if ref.views_at_age.method not in TRUE_AGE_METHODS) / len(refs)
            > PROXY_SHARE
        ):
            lowered = RANK_LABEL[max(0, LABEL_RANK[label] - 1)]
            if lowered is not label:
                caps.append("proxy_evidence")
                label = lowered
        matching = draft.expected_range.n_peer if draft.expected_range else len(draft.reference_pool)
        return Confidence(
            label=label,
            outliers=outliers,
            channels=channels,
            matching_videos=matching,
            rule_id=self.rule_id,
            caps_applied=caps,
        )

    @staticmethod
    def _caps(
        draft: RecommendationDraft, ctx: RecommendContext, refs: list
    ) -> list[tuple[str, ConfidenceLabel, bool]]:
        channel_share = max(Counter(ref.channel_id for ref in refs).values()) / len(refs) if refs else 0.0
        range_insufficient = draft.expected_range is not None and draft.expected_range.basis == "insufficient"
        return [
            ("contradictory", ConfidenceLabel.medium, "contradictory" in draft.flags),
            (
                "one_channel_dominates",
                ConfidenceLabel.medium,
                channel_share > ctx.settings.max_channel_ref_share,
            ),
            ("review_flagged_topics", ConfidenceLabel.medium, "review_flagged_topics" in draft.flags),
            ("stale_data", ConfidenceLabel.medium, ctx.stale_data),
            ("single_channel", ConfidenceLabel.low, "single_channel" in draft.flags),
            ("range_insufficient", ConfidenceLabel.low, range_insufficient),
        ]


@register_confidence_rule
class CreatorConfidenceRule:
    """creator_v1: one creator is one channel, so the 3-channel High rule cannot apply."""

    rule_id = ConfidenceRuleId.creator_v1
    applies_to = frozenset({RecType.creator})
    high_min_matching = 5
    high_min_outliers = 2
    high_max_gap_cv = 0.5

    def label(self, draft: RecommendationDraft, ctx: RecommendContext) -> Confidence:
        subject = draft.subject
        assert isinstance(subject, CreatorSubject)
        matching = len(draft.reference_pool)
        outliers = sum(1 for ref in draft.reference_pool if ref.is_outlier)
        caps: list[str] = []
        gap_cv = subject.consistency.gap_cv
        strong = matching >= self.high_min_matching and outliers >= self.high_min_outliers
        if strong and gap_cv is not None and gap_cv <= self.high_max_gap_cv:
            # the shared confidence object keeps High for 3+ channels, so a single creator tops out at Medium
            label = ConfidenceLabel.medium
            caps.append("single_creator_channel")
        elif matching >= MEDIUM_MIN_OUTLIERS and outliers >= 1:
            label = ConfidenceLabel.medium
        else:
            label = ConfidenceLabel.low
        if ctx.stale_data and label is not ConfidenceLabel.low:
            caps.append("stale_data")
        return Confidence(
            label=label,
            outliers=outliers,
            channels=1,
            matching_videos=matching,
            rule_id=self.rule_id,
            caps_applied=caps,
        )
