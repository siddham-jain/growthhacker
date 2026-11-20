import statistics
from dataclasses import dataclass
from typing import ClassVar, Protocol

from app.core.config import RecommendRangeSettings
from app.core.registry import Registry
from app.domain.common import FormatSegment
from app.domain.recommendations import (
    ExpectedRange,
    PatternSpec,
    RecommendationDraft,
    RecType,
    VideoIdeaSubject,
)
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.index import ClientBaseline

LABEL = "indicative, calculated by GrowthHacker"
INSUFFICIENT_DISPLAY = "not enough reference videos for a range"
NOT_APPLICABLE_DISPLAY = "not applicable"


class ExpectedRangeStrategy(Protocol):
    applies_to: ClassVar[frozenset[RecType]]

    def estimate(self, draft: RecommendationDraft, ctx: RecommendContext) -> "RangeEstimate": ...


expected_range_registry: Registry[type[ExpectedRangeStrategy]] = Registry("expected_range_registry")


def register_range_strategy[T: type](cls: T) -> T:
    expected_range_registry.add(cls.__name__, cls)
    return cls


def strategy_for(rec_type: RecType) -> ExpectedRangeStrategy | None:
    for key in sorted(expected_range_registry):
        strategy = expected_range_registry.get(key)
        if rec_type in strategy.applies_to:
            return strategy()
    return None


@dataclass(frozen=True, slots=True)
class RangeEstimate:
    range: ExpectedRange
    flags: tuple[str, ...]


def estimate_range(
    ratios: list[float],
    n_comparable: int,
    baseline: ClientBaseline | None,
    settings: RecommendRangeSettings,
    comparison_age_days: int,
) -> RangeEstimate:
    """simple maths: the reference videos' plain outlier ratios times the client's own median baseline."""
    flags: list[str] = []
    has_baseline = baseline is not None and baseline.n >= settings.min_client_baseline_n
    if has_baseline and baseline is not None and baseline.n < settings.provisional_baseline_n:
        flags.append("provisional_baseline")
    if len(ratios) < settings.min_references:
        return RangeEstimate(
            ExpectedRange(
                low=None,
                high=None,
                basis="insufficient",
                n_peer=n_comparable,
                n_peer_effective=float(len(ratios)),
                comparison_age_days=comparison_age_days,
                display=INSUFFICIENT_DISPLAY,
            ),
            tuple(flags),
        )
    # each ratio is held to a sensible band before the spread is read, so one viral video cannot set it
    capped = sorted(min(settings.cap, max(settings.floor, ratio)) for ratio in ratios)
    if len(capped) >= settings.quartile_min_n:
        first, _, third = statistics.quantiles(capped, n=4, method="inclusive")
    else:
        first, third = capped[0], capped[-1]
    low, high = round(first, 1), round(third, 1)
    span = f"{low:.1f}x" if low == high else f"{low:.1f}x-{high:.1f}x"
    if has_baseline and baseline is not None:
        display = f"{span} your usual views ({LABEL})"
        views_range = (round(low * baseline.views_at_age), round(high * baseline.views_at_age))
        basis = "peer"
    else:
        display = f"no baseline yet; the reference videos ran {span} their own channel's baseline ({LABEL})"
        views_range = None
        basis = "peer_only_no_baseline"
    return RangeEstimate(
        ExpectedRange(
            low=low,
            high=high,
            basis=basis,
            n_peer=n_comparable,
            n_peer_effective=float(len(capped)),
            comparison_age_days=comparison_age_days,
            views_at_age_range=views_range,
            display=display,
        ),
        tuple(flags),
    )


def _segment(format_key: str | None) -> FormatSegment | None:
    if not format_key:
        return None
    segment = format_key.partition("|")[0]
    return FormatSegment(segment) if segment in FormatSegment.__members__ else None


@register_range_strategy
class PeerRangeStrategy:
    applies_to = frozenset(
        {RecType.video_idea, RecType.title_pattern, RecType.thumbnail_pattern, RecType.format}
    )

    def estimate(self, draft: RecommendationDraft, ctx: RecommendContext) -> RangeEstimate:
        index = ctx.index
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject):
            comparable = index.by_topic(subject.topic_id)
            segment = _segment(subject.format_key)
        else:
            assert isinstance(subject, PatternSpec)
            comparable = index.with_traits(subject.traits)
            segment = _segment(subject.traits.get("format_key")) or (
                FormatSegment(subject.traits["segment"]) if "segment" in subject.traits else None
            )
        ratios = [ref.outlier_ratio for ref in draft.references if ref.outlier_ratio is not None]
        return estimate_range(
            ratios,
            len(comparable),
            index.client_baseline(segment),
            ctx.settings.expected_range,
            index.comparison_age_days,
        )


@register_range_strategy
class NotApplicableRangeStrategy:
    applies_to = frozenset({RecType.creator})

    def estimate(self, draft: RecommendationDraft, ctx: RecommendContext) -> RangeEstimate:
        not_applicable = ExpectedRange(
            low=None,
            high=None,
            basis="not_applicable",
            comparison_age_days=ctx.index.comparison_age_days,
            display=NOT_APPLICABLE_DISPLAY,
        )
        return RangeEstimate(not_applicable, ())
