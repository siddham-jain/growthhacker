"""which computed fields must have a policy row; the ci completeness test and the matrix footer share it."""

from pydantic import BaseModel

from app.domain.analytics import (
    ChannelScorecard,
    CommentSignals,
    OwnPerformanceReport,
    ThumbnailTraits,
    TraitLift,
    TrendingReport,
    VideoMetrics,
    VideoTraits,
)
from app.domain.news import NewsSignal
from app.domain.recommendations import ScoreBreakdown
from app.evaluation.policy.registry import FeatureRegistry
from app.services.recommend.scoring.components import score_component_registry
from app.services.recommend.scoring.penalties import penalty_registry

CHECKED_MODELS: tuple[type[BaseModel], ...] = (
    VideoMetrics,
    VideoTraits,
    ThumbnailTraits,
    CommentSignals,
    ChannelScorecard,
    TraitLift,
    OwnPerformanceReport,
    TrendingReport,
    NewsSignal,
    ScoreBreakdown,
)
# the scorer's open-ended dict fields are checked per registered component and penalty
SCORER_KEYED_FIELDS = {"components": score_component_registry, "penalties": penalty_registry}


def required_fields() -> list[str]:
    names = [f"{model.__name__}.{field}" for model in CHECKED_MODELS for field in model.model_fields]
    for field, registry in SCORER_KEYED_FIELDS.items():
        names += [f"{ScoreBreakdown.__name__}.{field}.{key}" for key in sorted(registry)]
    return names


def uncovered_fields(registry: FeatureRegistry) -> list[str]:
    covered = {name for spec in registry.all() for name in spec.covers}
    return [name for name in required_fields() if name not in covered]


def unknown_covers(registry: FeatureRegistry) -> list[str]:
    # a cover naming a renamed or removed field is stale and would hide a gap
    models = {model.__name__ for model in CHECKED_MODELS}
    known = set(required_fields())
    return sorted(
        f"{spec.key}: {name}"
        for spec in registry.all()
        for name in spec.covers
        if name.split(".", 1)[0] in models and name not in known
    )


def flag_rows_without_notes(registry: FeatureRegistry) -> list[str]:
    return [spec.key for spec in registry.flagged() if not (spec.review_note or "").strip()]


def policy_problems(registry: FeatureRegistry) -> list[str]:
    problems = list(registry.problems())
    problems += [f"no policy row covers {name}" for name in uncovered_fields(registry)]
    problems += [f"stale cover {name}" for name in unknown_covers(registry)]
    problems += [f"FLAG row without review note: {key}" for key in flag_rows_without_notes(registry)]
    return problems
