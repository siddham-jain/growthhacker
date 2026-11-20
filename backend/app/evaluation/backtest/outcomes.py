"""post-T outcomes (plan 12 §5.1.2 step 5): views at day a over the channel's normal frozen at T."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.analytics import TRUE_AGE_METHODS, AgeMethod
from app.evaluation.backtest.models import BacktestConfig, OutcomeVideo
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.result import AnalyticsResult
from app.services.recommend.evidence.traits import flatten_traits


@dataclass(frozen=True)
class FrozenBaseline:
    median: float
    method: str


class FrozenBaselines:
    """06's views-at-age baseline per channel and format as it stood at T; the latest-anchored sufficient
    baseline wins and a true-age baseline is preferred over a proxy one (families never mix silently)."""

    def __init__(self, by_key: dict[tuple[str, str], FrozenBaseline]) -> None:
        self.by_key = by_key

    @classmethod
    def from_result(
        cls, result: AnalyticsResult, inputs: AnalyticsInputs, age_days: int
    ) -> "FrozenBaselines":
        published = {v.video_id: v.published_at for v in inputs.videos}
        chosen: dict[tuple[str, str], tuple[tuple[bool, datetime, str], FrozenBaseline]] = {}
        for baseline in result.baselines:
            if baseline.metric != "views_at_age" or baseline.age_days != age_days or not baseline.sufficient:
                continue
            if baseline.median <= 0:
                continue
            anchored = published.get(baseline.anchor_video_id or "", baseline.as_of)
            rank = (baseline.method in TRUE_AGE_METHODS, anchored, baseline.baseline_id)
            key = (baseline.channel_id, baseline.segment.value)
            method = "true" if baseline.method in TRUE_AGE_METHODS else "proxy"
            if key not in chosen or rank > chosen[key][0]:
                chosen[key] = (rank, FrozenBaseline(baseline.median, method))
        return cls({key: value for key, (_, value) in chosen.items()})

    def get(self, channel_id: str, segment: str) -> FrozenBaseline | None:
        return self.by_key.get((channel_id, segment))

    def to_json(self) -> dict[str, dict[str, float | str]]:
        return {
            f"{c}|{s}": {"median": b.median, "method": b.method} for (c, s), b in sorted(self.by_key.items())
        }

    @classmethod
    def from_json(cls, payload: dict[str, dict[str, float | str]]) -> "FrozenBaselines":
        by_key = {}
        for key, value in payload.items():
            channel_id, _, segment = key.partition("|")
            by_key[(channel_id, segment)] = FrozenBaseline(float(value["median"]), str(value["method"]))
        return cls(by_key)


COMPLETE_METHODS = frozenset(TRUE_AGE_METHODS)


def collect_outcomes(
    config: BacktestConfig,
    baselines: FrozenBaselines,
    inputs: AnalyticsInputs,
    result: AnalyticsResult,
    universe: Iterable[str],
    own_channel_id: str | None,
) -> list[OutcomeVideo]:
    """every upload of the frozen universe published in (T, T + H]; values are never extrapolated."""
    start, end = config.as_of, config.as_of + timedelta(days=config.horizon_days)
    channels = set(universe)
    if not config.include_own_channel and own_channel_id:
        channels.discard(own_channel_id)
    age = config.outcome_age_days
    metrics = result.metrics.get(age, {})
    outcomes = []
    for video in sorted(inputs.videos, key=lambda v: (v.published_at, v.video_id)):
        if video.channel_id not in channels or not start < video.published_at <= end:
            continue
        base = {
            "video_id": video.video_id,
            "channel_id": video.channel_id,
            "client_id": config.client_id,
            "published_at": video.published_at,
        }
        if video.availability != "available" or video.privacy_status == "private":
            # removals skew towards underperformers; counted, never scored (edge case 3)
            outcomes.append(_empty(base, video.format.value, "deleted"))
            continue
        metric = metrics.get(video.video_id)
        if metric is None:
            outcomes.append(_empty(base, video.format.value, "missing_snapshot"))
            continue
        value = metric.views_at_age.get(age)
        method = value.method if value is not None else AgeMethod.unavailable
        traits = result.traits.get(video.video_id)
        shared = {
            **base,
            "segment": metric.segment.value,
            "views_at_age_method": method.value,
            "traits": flatten_traits(traits) if traits else {},
            "topic_ids": frozenset(t.topic_ref_id for t in traits.topics) if traits else frozenset(),
            "detector_ratio": metric.outlier.ratio
            if metric.outlier and metric.outlier.age_days == age
            else None,
            "detector_is_outlier": metric.outlier.is_outlier
            if metric.outlier and metric.outlier.age_days == age
            else None,
        }
        if value is None or value.views is None or method not in COMPLETE_METHODS:
            status = "pending" if method is AgeMethod.pending else "missing_snapshot"
            outcomes.append(_outcome(shared, None, None, None, status, config.outlier_threshold))
            continue
        baseline = baselines.get(video.channel_id, metric.segment.value)
        if baseline is None:
            outcomes.append(
                _outcome(shared, value.views, None, None, "no_baseline", config.outlier_threshold)
            )
            continue
        outcomes.append(
            _outcome(
                shared, value.views, baseline.median, baseline.method, "complete", config.outlier_threshold
            )
        )
    return outcomes


def _empty(base: dict, segment: str, status: str) -> OutcomeVideo:
    return OutcomeVideo(
        **base,
        segment=segment,
        views_at_age=None,
        views_at_age_method=AgeMethod.unavailable.value,
        baseline_at_t=None,
        baseline_method=None,
        ratio=None,
        is_outlier=None,
        status=status,
    )


def _outcome(
    shared: dict, views: int | None, baseline: float | None, method: str | None, status: str, threshold: float
) -> OutcomeVideo:
    ratio = views / baseline if views is not None and baseline else None
    return OutcomeVideo(
        **shared,
        views_at_age=views,
        baseline_at_t=baseline,
        baseline_method=method,
        ratio=ratio,
        is_outlier=None if ratio is None else ratio >= threshold,
        status=status,
    )
