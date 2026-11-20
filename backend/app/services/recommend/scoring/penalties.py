from typing import ClassVar, Protocol

from app.core.registry import Registry
from app.domain.recommendations import RecommendationDraft
from app.services.recommend.context import RecommendContext
from app.services.recommend.fingerprint import evidence_hash, feedback_group

CONTRADICTION_FACTOR = 0.7
SINGLE_CHANNEL_FACTOR = 0.8


class Penalty(Protocol):
    name: ClassVar[str]

    def factor(self, draft: RecommendationDraft, ctx: RecommendContext) -> float: ...


penalty_registry: Registry[type[Penalty]] = Registry("penalty_registry")


def register_penalty[T: type](cls: T) -> T:
    penalty_registry.add(cls.name, cls)
    return cls


@register_penalty
class FeedbackPenalty:
    name = "feedback"

    def factor(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        group = feedback_group(draft)
        factor = 1.0
        for penalty in ctx.config.feedback_penalties:
            if penalty.group_key == group and penalty.until > ctx.as_of:
                factor *= penalty.factor
        return factor


@register_penalty
class StalenessPenalty:
    name = "staleness"

    def factor(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        previous = ctx.previous_cards.get(draft.stable_key)
        if previous is None or previous.acted or previous.evidence_hash != evidence_hash(draft):
            return 1.0
        settings = ctx.settings.feedback
        days = (ctx.as_of - previous.unchanged_since).days - settings.staleness_grace_days
        return settings.staleness_base**days if days > 0 else 1.0


@register_penalty
class ContradictionPenalty:
    name = "contradiction"

    def factor(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        return CONTRADICTION_FACTOR if "contradictory" in draft.flags else 1.0


@register_penalty
class SingleChannelPenalty:
    name = "single_channel"

    def factor(self, draft: RecommendationDraft, ctx: RecommendContext) -> float:
        return SINGLE_CHANNEL_FACTOR if "single_channel" in draft.flags else 1.0
