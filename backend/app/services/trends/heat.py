import statistics
from collections.abc import Sequence

from app.domain.trends import Heat, KeywordTrend, TrendScan
from app.services.news.text import normalize


def _heat(
    trend: KeywordTrend, niche_median: float | None, hot_ratio: float, min_videos: int, min_channels: int
) -> Heat:
    if (
        trend.leaders_views_per_day is None
        or niche_median is None
        or niche_median <= 0
        or trend.recent_videos < min_videos
        or trend.channels < min_channels
    ):
        return "quiet"
    ratio = trend.leaders_views_per_day / niche_median
    if ratio >= hot_ratio or (trend.momentum is not None and trend.momentum >= hot_ratio):
        return "hot"
    return "warm" if ratio >= 1.0 else "quiet"


def score(
    trends: Sequence[KeywordTrend],
    previous: TrendScan | None,
    *,
    hot_ratio: float,
    min_videos: int,
    min_channels: int,
) -> list[KeywordTrend]:
    """heat = a keyword's leading videos' views/day against the median of all keywords checked for this client."""
    before = {
        normalize(item.candidate.keyword): item
        for item in (previous.keywords if previous else [])
        if item.leaders_views_per_day
    }
    with_momentum = []
    for trend in trends:
        old = before.get(normalize(trend.candidate.keyword))
        # a carried-over check keeps the momentum it had when it was measured
        if old is None or old.checked_at >= trend.checked_at:
            with_momentum.append(trend)
            continue
        momentum = (
            round(trend.leaders_views_per_day / old.leaders_views_per_day, 2)
            if trend.leaders_views_per_day
            else None
        )
        with_momentum.append(trend.model_copy(update={"momentum": momentum}))
    rates = [t.leaders_views_per_day for t in with_momentum if t.leaders_views_per_day]
    niche_median = statistics.median(rates) if rates else None
    scored = [
        t.model_copy(update={"heat": _heat(t, niche_median, hot_ratio, min_videos, min_channels)})
        for t in with_momentum
    ]
    order = {"hot": 0, "warm": 1, "quiet": 2}
    return sorted(scored, key=lambda t: (order[t.heat], -(t.leaders_views_per_day or 0), t.candidate.keyword))
