"""the client's own channel as an evidence-sheet column, read from 06's published per-video metrics.

06 builds scorecards for tracked channels only, so the "You" column applies the same raw definitions
to the own channel's videos in the window. no score and no rank, like every sheet.
"""

from collections import Counter
from datetime import datetime, timedelta

from app.domain.analytics import ChannelScorecard, MedianAtAge, VideoMetrics
from app.domain.common import ChannelRole, FormatSegment, subscriber_band
from app.services.analytics.patterns.stats import median, wilson
from app.services.analytics.reports.common import coefficient_of_variation, gaps_in_days
from app.services.analytics.result import AnalyticsResult, WindowAnalytics
from app.services.analytics.settings import AnalyticsEngineSettings


def _median_views(assessed: list[VideoMetrics]) -> dict[FormatSegment, MedianAtAge]:
    grouped: dict[FormatSegment, list[VideoMetrics]] = {}
    for metrics in assessed:
        grouped.setdefault(metrics.segment, []).append(metrics)
    result = {}
    for segment, items in sorted(grouped.items()):
        methods = Counter(m.outlier.method for m in items if m.outlier)
        method = min(methods, key=lambda m: (-methods[m], m.value))
        values = [m.outlier.value for m in items if m.outlier]
        result[segment] = MedianAtAge(value=round(median(values)), method=method, n=len(items))
    return result


def _engagement(videos: list[VideoMetrics]) -> float | None:
    # compared only within one basis, so the channel's most common basis is used
    rated = [m for m in videos if m.engagement_rate is not None]
    if not rated:
        return None
    bases = Counter(m.engagement_basis for m in rated)
    basis = min(bases, key=lambda b: (-bases[b], b))
    return median([m.engagement_rate for m in rated if m.engagement_basis == basis])


def own_scorecard(
    result: AnalyticsResult,
    window: WindowAnalytics,
    channel_id: str,
    subscriber_count: int | None,
    published: list[datetime],
    settings: AnalyticsEngineSettings,
) -> ChannelScorecard:
    """`published` holds the channel's upload times up to as_of, for cadence and recency."""
    as_of, days = result.as_of, window.window_days
    since = as_of - timedelta(days=days)
    metrics = result.metrics.get(window.comparison_age_days, {}).values()
    videos = [m for m in metrics if m.channel_id == channel_id and since <= m.age_anchor_at <= as_of]
    assessed = [m for m in videos if m.outlier is not None]
    outliers = sum(m.outlier.is_outlier for m in assessed if m.outlier)
    lookback = as_of - timedelta(days=max(settings.reports.cadence_lookback_days, days))
    recent = sorted(at for at in published if lookback <= at <= as_of)
    gaps = gaps_in_days(recent)
    last = max(published, default=None)
    segments = Counter(m.segment for m in videos)
    return ChannelScorecard(
        channel_id=channel_id,
        client_id=result.client_id,
        role=ChannelRole.own,
        window_days=days,
        as_of=as_of,
        uploads_in_window=len(videos),
        uploads_per_week=len(videos) / (days / 7),
        gap_cv=coefficient_of_variation(gaps)
        if len(recent) >= settings.reports.min_uploads_for_gap_cv
        else None,
        longest_gap_days=max(gaps, default=None),
        days_since_last_upload=(as_of - last).total_seconds() / 86_400 if last else None,
        segment_mix={segment: segments[segment] / len(videos) for segment in sorted(segments)},
        median_views_at_age=_median_views(assessed),
        median_ratio=median([m.outlier.ratio for m in assessed if m.outlier]) if assessed else None,
        outlier_hit_rate=(outliers, len(assessed)),
        outlier_hit_rate_wilson=wilson(outliers, len(assessed)),
        median_engagement_rate=_engagement(videos),
        topic_coverage={
            ref_id: row[channel_id]
            for ref_id, row in sorted(window.gap_matrix.cells.items())
            if channel_id in row
        },
        subscriber_count=subscriber_count,
        subscriber_band=subscriber_band(subscriber_count),
        data_sufficiency={"uploads_in_window": bool(videos), "baseline": bool(assessed)},
    )
