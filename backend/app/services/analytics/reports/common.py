import math
from collections.abc import Iterable, Sequence

from app.domain.analytics import AgeMethod, OutlierAssessment, ViewsAtAgeSummary
from app.domain.common import DirectnessLevel
from app.services.analytics.ages import age_label
from app.services.analytics.patterns.stats import median, quantiles, wilson
from app.services.analytics.state import VideoState

DIRECTNESS_RANK = {
    DirectnessLevel.direct: 0,
    DirectnessLevel.partial: 1,
    DirectnessLevel.mention: 2,
    DirectnessLevel.none: 3,
}


def ratio_key(state: VideoState, assessment: OutlierAssessment) -> tuple[float, object, str]:
    # full tie-break: ratio desc, published_at asc, video_id asc
    return (-assessment.ratio, state.published_at, state.video_id)


def views_summary(state: VideoState, assessment: OutlierAssessment) -> ViewsAtAgeSummary:
    if assessment.method is AgeMethod.age_band_proxy:
        observed = state.series.latest.age_days
        return ViewsAtAgeSummary(
            age_days=max(0, round(observed)),
            views=assessment.value,
            method=assessment.method,
            label=age_label(assessment.method, assessment.age_days, observed),
        )
    return ViewsAtAgeSummary(
        age_days=assessment.age_days,
        views=assessment.value,
        method=assessment.method,
        label=age_label(assessment.method, assessment.age_days, None),
    )


def gaps_in_days(published: Sequence[object]) -> list[float]:
    ordered = sorted(published)
    return [
        (later - earlier).total_seconds() / 86_400
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ]


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def iqr(values: Sequence[float]) -> tuple[float, float] | None:
    if not values:
        return None
    p25, _, p75 = quantiles(values)
    return (p25, p75)


def log_iqr(ratios: Iterable[float]) -> float | None:
    logs = [math.log10(r) for r in ratios if r > 0]
    if len(logs) < 2:
        return None
    p25, _, p75 = quantiles(logs)
    return p75 - p25


def best_directness(levels: Iterable[DirectnessLevel | None]) -> DirectnessLevel | None:
    present = [level for level in levels if level is not None]
    return min(present, key=lambda level: DIRECTNESS_RANK[level]) if present else None


__all__ = ["median", "quantiles", "wilson"]
