import math
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
from app.services.recommend.evidence.index import ClientBaseline, EvidenceVideo
from app.services.recommend.recommenders.base import own_with_traits
from app.services.recommend.stats import ceil_1dp, effective_n, floor_1dp, weighted_quantile

LN_10 = math.log(10)
INSUFFICIENT_DISPLAY = "not enough comparable videos for a range"
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
    peers: list[EvidenceVideo],
    own_ratios: list[float],
    baseline: ClientBaseline | None,
    settings: RecommendRangeSettings,
    comparison_age_days: int,
) -> RangeEstimate:
    """middle half of every comparable video's ratio, shrunk toward 1.0 by sample size; never a promise."""
    flags: list[str] = []
    has_baseline = baseline is not None and baseline.n >= settings.min_client_baseline_n
    if has_baseline and baseline is not None and baseline.n < settings.provisional_baseline_n:
        flags.append("provisional_baseline")
    clip_low, clip_high = settings.ratio_clip_low, settings.ratio_clip_high
    xs = [math.log(min(clip_high, max(clip_low, video.ratio))) for video in peers]
    if has_baseline and baseline is not None:
        distances = [
            abs(math.log(max(video.baseline_median, 1.0) / max(baseline.views_at_age, 1.0)))
            for video in peers
        ]
        weights = [math.exp(-distance / LN_10) for distance in distances]
    else:
        distances = []
        weights = [1.0] * len(peers)
    n_eff = effective_n(weights)
    if n_eff < settings.n_min:
        return RangeEstimate(
            ExpectedRange(
                low=None,
                high=None,
                basis="insufficient",
                n_peer=len(peers),
                n_peer_effective=round(n_eff, 1),
                n_own=len(own_ratios),
                comparison_age_days=comparison_age_days,
                display=INSUFFICIENT_DISPLAY,
            ),
            tuple(flags),
        )
    shrink = n_eff / (n_eff + settings.k_peer)
    low = shrink * weighted_quantile(xs, weights, 0.25)
    high = shrink * weighted_quantile(xs, weights, 0.75)
    basis = "peer" if has_baseline else "peer_only_no_baseline"
    if has_baseline and len(own_ratios) >= settings.own_min_n:
        own_xs = [math.log(min(clip_high, max(clip_low, ratio))) for ratio in own_ratios]
        n_own = len(own_xs)
        own_shrink = n_own / (n_own + settings.k_own)
        own_low = own_shrink * weighted_quantile(own_xs, [1.0] * n_own, 0.25)
        own_high = own_shrink * weighted_quantile(own_xs, [1.0] * n_own, 0.75)
        alpha = 1.0 if n_own >= settings.own_only_n else n_own / (n_own + settings.k_blend)
        low = alpha * own_low + (1 - alpha) * low
        high = alpha * own_high + (1 - alpha) * high
        basis = "own" if alpha == 1.0 else "blended"
    cap = settings.cap
    size_note = None
    if distances:
        within = sum(weight for weight, distance in zip(weights, distances, strict=True) if distance <= LN_10)
        if within / sum(weights) < settings.size_mismatch_share:
            cap = settings.cap_size_mismatch
            flags.append("size_mismatch")
            peer_sizes = sorted(video.baseline_median for video in peers)
            assert baseline is not None
            larger = peer_sizes[len(peer_sizes) // 2] > baseline.views_at_age
            size_note = "peers_much_larger" if larger else "peers_much_smaller"
    low, high = _bounded(low, high, math.log(settings.floor), math.log(cap), math.log(settings.min_width))
    low_x, high_x = floor_1dp(math.exp(low)), ceil_1dp(math.exp(high))
    span = f"{low_x:.1f}x-{high_x:.1f}x"
    if has_baseline and baseline is not None:
        display = f"{span} (indicative)"
        views_range = (
            round(math.exp(low) * baseline.views_at_age),
            round(math.exp(high) * baseline.views_at_age),
        )
    else:
        display = f"no baseline yet; similar videos ran {span} their own channel's baseline (indicative)"
        views_range = None
    return RangeEstimate(
        ExpectedRange(
            low=low_x,
            high=high_x,
            basis=basis,
            n_peer=len(peers),
            n_peer_effective=round(n_eff, 1),
            n_own=len(own_ratios),
            comparison_age_days=comparison_age_days,
            size_note=size_note,
            views_at_age_range=views_range,
            display=display,
        ),
        tuple(flags),
    )


def _bounded(low: float, high: float, floor: float, cap: float, min_width: float) -> tuple[float, float]:
    low, high = max(low, floor), min(high, cap)
    if high < low:
        high = low
    if high - low < min_width:
        middle = (low + high) / 2
        low, high = middle - min_width / 2, middle + min_width / 2
        if high > cap:
            low, high = cap - min_width, cap
        if low < floor:
            low, high = floor, floor + min_width
    return low, high


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
        settings = ctx.settings.expected_range
        index = ctx.index
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject):
            topical = index.by_topic(subject.topic_id)
            peers = [video for video in topical if subject.format_key in (None, video.format_key)]
            if len(peers) < settings.fallback_min_matching:
                peers = topical
            own = [
                video.ratio
                for video in index.own_by_topic(subject.topic_id)
                if video.ratio is not None and subject.format_key in (None, video.format_key)
            ]
            segment = _segment(subject.format_key)
        else:
            assert isinstance(subject, PatternSpec)
            with_traits = index.with_traits(subject.traits)
            peers = [video for video in with_traits if video.topic_ids]
            if len(peers) < settings.fallback_min_matching:
                peers = with_traits
            own = [
                video.ratio
                for video in own_with_traits(index.own_videos, subject.traits)
                if video.ratio is not None
            ]
            segment = _segment(subject.traits.get("format_key")) or (
                FormatSegment(subject.traits["segment"]) if "segment" in subject.traits else None
            )
        return estimate_range(peers, own, index.client_baseline(segment), settings, index.comparison_age_days)


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
