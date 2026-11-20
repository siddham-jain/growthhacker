import math
from typing import ClassVar, Protocol

from app.core.registry import Registry
from app.domain.recommendations import PatternSpec, RecommendationDraft, VideoIdeaSubject
from app.services.recommend.confidence import outlier_counts
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.lifts import LiftBook, ci_low
from app.services.recommend.stats import clip01, median
from app.services.recommend.topics import KEYWORD_INTENT

LOW_USAGE_SHARE = 0.2
LOW_USAGE_WEIGHT = 0.6


class ScoreComponent(Protocol):
    name: ClassVar[str]

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float: ...


score_component_registry: Registry[type[ScoreComponent]] = Registry("score_component_registry")


def register_component[T: type](cls: T) -> T:
    score_component_registry.add(cls.name, cls)
    return cls


@register_component
class EvidenceStrength:
    name = "evidence"

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        outliers, channels = outlier_counts(draft)
        ratio = median(ref.outlier_ratio or 0.0 for ref in draft.references) or 0.0
        lift_low = self._lift_low(draft, ctx)
        return (
            0.35 * (1 - math.exp(-outliers / 5))
            + 0.25 * min(1.0, channels / 4)
            + 0.20 * clip01(math.log(ratio) / math.log(5) if ratio > 0 else 0.0)
            + 0.20 * clip01(math.log(lift_low) / math.log(3) if lift_low > 0 else 0.0)
        )

    @staticmethod
    def _lift_low(draft: RecommendationDraft, ctx: RecommendContext) -> float:
        subject = draft.subject
        if isinstance(subject, PatternSpec):
            return subject.ci_low
        if isinstance(subject, VideoIdeaSubject) and subject.format_key:
            lift = LiftBook(ctx.lifts).best_supported("format_key", subject.format_key, 3)
            return (ci_low(lift) or 0.0) if lift else 0.0
        return 0.0


@register_component
class ClientFit:
    name = "fit"

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        return 0.6 * self._topic_overlap(draft, ctx) + 0.4 * self._own_signal(draft, ctx)

    @staticmethod
    def _topic_overlap(draft: RecommendationDraft, ctx: RecommendContext) -> float:
        refs = draft.references
        best = 0.0
        for topic_id in draft.topic_ids:
            topic = ctx.catalog.get(topic_id)
            if topic is None or not refs:
                continue
            share = sum(1 for ref in refs if topic_id in ref.matched_topic_ids) / len(refs)
            best = max(best, topic.weight * (1 - ctx.index.breadth(topic_id)) * share)
        return best

    @staticmethod
    def _own_signal(draft: RecommendationDraft, ctx: RecommendContext) -> float:
        subject = draft.subject
        minimum = ctx.settings.expected_range.own_min_n
        ratios: list[float] = []
        if isinstance(subject, VideoIdeaSubject):
            ratios = [
                video.ratio
                for video in ctx.index.own_by_topic(subject.topic_id)
                if video.ratio is not None and subject.format_key in (None, video.format_key)
            ]
        elif (
            isinstance(subject, PatternSpec)
            and subject.client_ratio_with is not None
            and subject.client_n_with >= minimum
        ):
            return clip01((subject.client_ratio_with - 0.5) / 1.5)
        if len(ratios) < minimum:
            return 0.5
        return clip01(((median(ratios) or 0.0) - 0.5) / 1.5)


@register_component
class GapSize:
    name = "gap"

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        if draft.gap is not None:
            return draft.gap.gap_size
        subject = draft.subject
        share = subject.client_usage_share if isinstance(subject, PatternSpec) else None
        if share is not None and share < LOW_USAGE_SHARE:
            return (1 - share / LOW_USAGE_SHARE) * LOW_USAGE_WEIGHT
        return 0.0


@register_component
class IntentStage:
    name = "intent"

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        topic = ctx.catalog.for_question(draft.buyer_question_id)
        if topic is not None:
            return topic.intent
        intents = [
            topic.intent
            for ref in draft.references
            for topic_id in ref.matched_topic_ids
            if (topic := ctx.catalog.get(topic_id)) is not None
        ]
        return sum(intents) / len(intents) if intents else KEYWORD_INTENT


@register_component
class Recency:
    name = "recency"

    def value(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        refs = draft.references
        if not refs:
            return 0.0
        half_life = ctx.window_days / 3
        ages = [(ctx.as_of - ref.published_at).total_seconds() / 86_400 for ref in refs]
        return sum(2 ** (-max(0.0, age) / half_life) for age in ages) / len(ages)
