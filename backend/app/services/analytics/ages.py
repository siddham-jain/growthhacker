from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.registry import Registry
from app.domain.analytics import AgeMethod, ViewsAtAge
from app.domain.youtube import VideoRecord
from app.services.analytics.settings import AgeSettings
from app.services.analytics.snapshots import CleanSeries, CleanSnapshot

ANALYTICS_DAY_TZ = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True, slots=True)
class OwnerDailyViews:
    # owner-authorised daily views on the analytics api's pacific-time day boundaries
    days: Mapping[date, int]
    data_through: date
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class AgeContext:
    video: VideoRecord
    series: CleanSeries
    anchor: datetime
    as_of: datetime
    age_now: float
    settings: AgeSettings
    owner_daily: OwnerDailyViews | None = None


class AgeResolutionStrategy(Protocol):
    method: AgeMethod

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None: ...


age_strategy_registry: Registry[type[AgeResolutionStrategy]] = Registry("age_strategy_registry")
DEFAULT_ORDER: tuple[str, ...] = (
    "owner_analytics",
    "observed",
    "interpolated",
    "pending",
    "age_band_proxy",
    "unavailable",
)


def age_label(method: AgeMethod, target_age: int, observed_age: float | None) -> str:
    if method is AgeMethod.age_band_proxy and observed_age is not None:
        return f"views at {round(observed_age)} days (proxy)"
    labels = {
        AgeMethod.observed: f"views at day {target_age} (observed)",
        AgeMethod.interpolated: f"views at day {target_age} (interpolated)",
        AgeMethod.owner_analytics: f"views at day {target_age} (YouTube Analytics)",
        AgeMethod.pending: "too new to judge",
        AgeMethod.unavailable: f"views at day {target_age} not available",
    }
    return labels[method]


def _build(
    ctx: AgeContext,
    target_age: int,
    method: AgeMethod,
    *,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    observed_age: float | None = None,
    lo: CleanSnapshot | None = None,
    hi: CleanSnapshot | None = None,
    spread: float | None = None,
    source: str = "data_api_snapshot",
    source_oldest_at: datetime | None = None,
) -> ViewsAtAge:
    return ViewsAtAge(
        video_id=ctx.video.video_id,
        age_days=target_age,
        views=views,
        likes=likes,
        comments=comments,
        method=method,
        observed_age_days=observed_age,
        bracket_lo_at=lo.captured_at if lo else None,
        bracket_hi_at=hi.captured_at if hi else None,
        bracket_spread=spread,
        source=source,
        label=age_label(method, target_age, observed_age),
        as_of=ctx.as_of,
        source_oldest_at=source_oldest_at or ctx.as_of,
    )


def bracket(points: Sequence[CleanSnapshot], age: float) -> tuple[CleanSnapshot, CleanSnapshot] | None:
    lower = [p for p in points if p.age_days <= age]
    upper = [p for p in points if p.age_days >= age]
    if not lower or not upper:
        return None
    return lower[-1], upper[0]


def interpolate(lo: CleanSnapshot, hi: CleanSnapshot, age: float, field: str = "views") -> float | None:
    low, high = getattr(lo, field), getattr(hi, field)
    if low is None or high is None:
        return None
    if hi.age_days == lo.age_days:
        return float(high)
    return low + (high - low) * (age - lo.age_days) / (hi.age_days - lo.age_days)


def views_at_exact_age(series: CleanSeries, age: float, max_gap_days: float) -> float | None:
    found = bracket(series.points, age)
    if found is None:
        return None
    lo, hi = found
    if hi.age_days - lo.age_days > max_gap_days:
        return None
    return interpolate(lo, hi, age)


def nearest_day_end(anchor: datetime, target_age: int) -> tuple[date, float]:
    """the pacific day whose midnight is closest to anchor + target_age days, and that midnight's real age."""
    target = (anchor + timedelta(days=target_age)).astimezone(ANALYTICS_DAY_TZ)
    before = datetime.combine(target.date(), time.min, ANALYTICS_DAY_TZ)
    after = datetime.combine(target.date() + timedelta(days=1), time.min, ANALYTICS_DAY_TZ)
    end = before if target - before <= after - target else after
    return end.date() - timedelta(days=1), (end - anchor).total_seconds() / 86_400


@age_strategy_registry.register("owner_analytics")
class OwnerAnalyticsStrategy:
    # day n is the sum of whole pacific days up to the midnight nearest the real age n, held to the
    # observed ladder's tolerance; any missing day means the series is incomplete and the ladder falls back
    method = AgeMethod.owner_analytics

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        owner = ctx.owner_daily
        if owner is None or ctx.age_now < target_age:
            return None
        first_day = ctx.anchor.astimezone(ANALYTICS_DAY_TZ).date()
        last_day, covered_age = nearest_day_end(ctx.anchor, target_age)
        if last_day < first_day or last_day > owner.data_through:
            return None
        if abs(covered_age - target_age) > ctx.settings.tol_days(target_age):
            return None
        days = [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]
        if any(day not in owner.days for day in days):
            return None
        return _build(
            ctx,
            target_age,
            self.method,
            views=sum(owner.days[day] for day in days),
            observed_age=covered_age,
            source="analytics_api",
            source_oldest_at=owner.verified_at,
        )


@age_strategy_registry.register("observed")
class ObservedStrategy:
    method = AgeMethod.observed

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        tol = ctx.settings.tol_days(target_age)
        near = [p for p in ctx.series.points if abs(p.age_days - target_age) <= tol]
        if not near:
            return None
        best = min(near, key=lambda p: (abs(p.age_days - target_age), p.captured_at))
        return _build(
            ctx,
            target_age,
            self.method,
            views=best.views,
            likes=best.likes,
            comments=best.comments,
            observed_age=best.age_days,
            source_oldest_at=best.captured_at,
        )


@age_strategy_registry.register("interpolated")
class InterpolatedStrategy:
    method = AgeMethod.interpolated

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        found = bracket(ctx.series.points, target_age)
        if found is None:
            return None
        lo, hi = found
        if hi.age_days - lo.age_days > ctx.settings.max_gap_hours[target_age] / 24:
            return None
        value = interpolate(lo, hi, target_age)
        if value is None:
            return None
        spread = (hi.views - lo.views) / value if value > 0 else 0.0
        if spread > ctx.settings.max_bracket_spread:
            return None
        likes = interpolate(lo, hi, target_age, "likes")
        comments = interpolate(lo, hi, target_age, "comments")
        return _build(
            ctx,
            target_age,
            self.method,
            views=int(round(value)),
            likes=None if likes is None else int(round(likes)),
            comments=None if comments is None else int(round(comments)),
            observed_age=float(target_age),
            lo=lo,
            hi=hi,
            spread=spread,
            source_oldest_at=lo.captured_at,
        )


@age_strategy_registry.register("pending")
class PendingStrategy:
    method = AgeMethod.pending

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        tol = ctx.settings.tol_days(target_age)
        latest = ctx.series.latest
        waiting_for_milestone = ctx.age_now < target_age + tol and (
            latest is None or latest.age_days < target_age
        )
        if ctx.age_now < target_age - tol or waiting_for_milestone:
            return _build(ctx, target_age, self.method)
        return None


@age_strategy_registry.register("age_band_proxy")
class AgeBandProxyStrategy:
    # the current count at its real age; compared only against age-matched peers, never extrapolated
    method = AgeMethod.age_band_proxy

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        latest = ctx.series.latest
        if latest is None or latest.age_days < target_age:
            return None
        return _build(
            ctx,
            target_age,
            self.method,
            views=latest.views,
            likes=latest.likes,
            comments=latest.comments,
            observed_age=latest.age_days,
            source_oldest_at=latest.captured_at,
        )


@age_strategy_registry.register("unavailable")
class UnavailableStrategy:
    method = AgeMethod.unavailable

    def resolve(self, ctx: AgeContext, target_age: int) -> ViewsAtAge | None:
        return _build(ctx, target_age, self.method)


class ViewsAtAgeResolver:
    """first strategy that succeeds wins; never mixes sources and never extrapolates."""

    def __init__(self, settings: AgeSettings, order: Sequence[str] = DEFAULT_ORDER) -> None:
        self.settings = settings
        self.strategies = [age_strategy_registry.get(key)() for key in order]

    def resolve_age(self, ctx: AgeContext, target_age: int) -> ViewsAtAge:
        for strategy in self.strategies:
            value = strategy.resolve(ctx, target_age)
            if value is not None:
                return value
        raise LookupError("no age strategy resolved; the unavailable strategy must be last")

    def resolve(self, ctx: AgeContext) -> dict[int, ViewsAtAge]:
        return {age: self.resolve_age(ctx, age) for age in self.settings.target_ages_days}
