from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.analytics import AgeMethod
from app.services.analytics.ages import (
    AgeContext,
    ObservedStrategy,
    OwnerAnalyticsStrategy,
    OwnerDailyViews,
    ViewsAtAgeResolver,
)
from app.services.analytics.settings import AgeSettings
from app.services.analytics.snapshots import CleanSeries, CleanSnapshot

SETTINGS = AgeSettings()
VIDEO = SimpleNamespace(video_id="abcdefghijk")
# 18:00 utc is 10:00 pacific on 1 nov
ANCHOR = datetime(2026, 11, 1, 18, tzinfo=UTC)
AS_OF = ANCHOR + timedelta(days=10)
SERIES = CleanSeries(
    tuple(CleanSnapshot(ANCHOR + timedelta(days=d), d, 1000 * d, None, None) for d in range(1, 11)), False, ()
)


def context(days: dict[date, int], anchor: datetime = ANCHOR) -> AgeContext:
    owner = OwnerDailyViews(days=days, data_through=date(2026, 11, 9), verified_at=AS_OF)
    return AgeContext(VIDEO, SERIES, anchor, anchor + timedelta(days=10), 10.0, SETTINGS, owner)


def daily(first: date, count: int, views: int = 100) -> dict[date, int]:
    return {first + timedelta(days=offset): views for offset in range(count)}


def test_a_video_without_owner_rows_falls_back_to_the_observed_value() -> None:
    ctx = context({})
    assert OwnerAnalyticsStrategy().resolve(ctx, 7) is None
    resolved = ViewsAtAgeResolver(SETTINGS).resolve_age(ctx, 7)
    assert (
        resolved.method is AgeMethod.observed and resolved.views == ObservedStrategy().resolve(ctx, 7).views
    )


def test_a_series_that_stops_early_never_overrides_the_observed_value() -> None:
    ctx = context({date(2026, 11, 1): 500, date(2026, 11, 2): 1000})
    assert OwnerAnalyticsStrategy().resolve(ctx, 7) is None
    assert ViewsAtAgeResolver(SETTINGS).resolve_age(ctx, 7).method is AgeMethod.observed


def test_day_n_ends_at_the_pacific_midnight_nearest_the_real_age() -> None:
    # 23:30 pacific on 1 nov: day 3 is the 1-4 nov days, which end 3.02 days after publish
    late = datetime(2026, 11, 2, 7, 30, tzinfo=UTC)
    resolved = OwnerAnalyticsStrategy().resolve(context(daily(date(2026, 11, 1), 10), late), 3)
    assert resolved.views == 400
    assert resolved.observed_age_days == pytest.approx(3 + 0.5 / 24, abs=1e-6)


def test_a_day_boundary_outside_the_observed_tolerance_is_not_used() -> None:
    # 10:00 pacific publish: the nearest midnight to day 3 is 10 h away, past the 6 h tolerance
    assert OwnerAnalyticsStrategy().resolve(context(daily(date(2026, 11, 1), 10)), 3) is None
    week = OwnerAnalyticsStrategy().resolve(context(daily(date(2026, 11, 1), 10)), 7)
    assert week.views == 700 and week.method is AgeMethod.owner_analytics
