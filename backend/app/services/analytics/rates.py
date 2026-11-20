from dataclasses import dataclass
from typing import Literal

from app.domain.analytics import Velocity
from app.services.analytics.settings import RateSettings
from app.services.analytics.snapshots import CleanSeries, CleanSnapshot

EngagementBasis = Literal["likes+comments", "likes_only", "comments_only", "none"]


@dataclass(frozen=True, slots=True)
class Rates:
    like_rate: float | None
    comment_rate: float | None
    engagement_rate: float | None
    basis: EngagementBasis
    low_views: bool


def engagement_basis(likes: int | None, comments: int | None) -> EngagementBasis:
    if likes is not None and comments is not None:
        return "likes+comments"
    if likes is not None:
        return "likes_only"
    if comments is not None:
        return "comments_only"
    return "none"


def compute_rates(views: int, likes: int | None, comments: int | None, settings: RateSettings) -> Rates:
    # hidden likes and disabled comments arrive as None and are never treated as zero
    basis = engagement_basis(likes, comments)
    if views < settings.min_views_for_rates:
        return Rates(None, None, None, basis, low_views=True)
    like_rate = None if likes is None else likes / views
    comment_rate = None if comments is None else comments / views
    engaged = [value for value in (likes, comments) if value is not None]
    engagement = sum(engaged) / views if engaged else None
    return Rates(like_rate, comment_rate, engagement, basis, low_views=False)


def lifetime_views_per_day(views: int, age_days: float) -> float:
    return views / max(age_days, 1.0)


def velocity_between(start: CleanSnapshot, end: CleanSnapshot) -> Velocity:
    hours = (end.captured_at - start.captured_at).total_seconds() / 3600
    return Velocity(
        views_per_day=(end.views - start.views) / (hours / 24),
        from_at=start.captured_at,
        to_at=end.captured_at,
        window_hours=hours,
    )


def _span_hours(start: CleanSnapshot, end: CleanSnapshot) -> float:
    return (end.captured_at - start.captured_at).total_seconds() / 3600


def recent_velocity(series: CleanSeries, settings: RateSettings) -> Velocity | None:
    end = series.latest
    if end is None:
        return None
    starts = list(series.points[:-1])
    low, high = settings.recent_window_h
    last_day = [p for p in starts if low <= _span_hours(p, end) <= high]
    if last_day:
        return velocity_between(max(last_day, key=lambda p: p.captured_at), end)
    usable = [p for p in starts if _span_hours(p, end) >= settings.min_velocity_interval_h]
    if not usable:
        return None
    return velocity_between(max(usable, key=lambda p: p.captured_at), end)


def week_velocity(series: CleanSeries, settings: RateSettings) -> Velocity | None:
    end = series.latest
    if end is None:
        return None
    week = [p for p in series.points[:-1] if _span_hours(p, end) >= settings.velocity_7d_min_days * 24]
    if not week:
        return None
    return velocity_between(max(week, key=lambda p: p.captured_at), end)
