from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import YouTubeAnalyticsApiSettings
from app.models.own_analytics import OwnAnalyticsDaily
from app.ports.youtube import AnalyticsQuery, AnalyticsTable
from app.services.youtube.own_analytics import OwnAnalyticsService
from tests.youtube.fixtures import CHANNEL_A, NOW, channel_record, video_id, video_record
from tests.youtube.test_own_analytics import (  # noqa: F401  fixtures
    CTX,
    FakeFlow,
    FakeReports,
    cipher,
    connect,
    service,
    tenant,
)

pytestmark = pytest.mark.db

RECENT = video_id(2)
MISSING_DAY = date(2026, 11, 17)


class SeriesReports(FakeReports):
    """a fresh upload outside the top list, whose series has a day the api left out."""

    async def report(self, access_token, query: AnalyticsQuery, ctx=None) -> AnalyticsTable:
        if query.dimensions == ["day"] and query.filters == f"video=={RECENT}":
            self.queries.append(query)
            days = [date(2026, 11, 15) + timedelta(days=offset) for offset in range(4)]
            rows = [[day.isoformat(), 100] for day in days if day != MISSING_DAY]
            return AnalyticsTable(column_headers=["day", "views"], rows=rows)
        return await super().report(access_token, query, ctx)


def series_pulls(reports: FakeReports) -> list[str]:
    return [query.filters for query in reports.queries if query.dimensions == ["day"] and query.filters]


async def test_recent_uploads_get_a_series_and_incomplete_series_are_pulled_again(
    uow_factory,
    redis,
    redis_prefix,
    clock,
    cipher,  # noqa: F811
    tenant,  # noqa: F811
    session,
    store,
) -> None:
    await store.save_channels([channel_record(CHANNEL_A)])
    await store.save_videos([video_record(RECENT, channel_id=CHANNEL_A, published=NOW - timedelta(days=5))])
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, FakeFlow(clock), CHANNEL_A)
    await connect(connections, tenant)
    reports = SeriesReports()
    analytics = OwnAnalyticsService(connections, reports, uow_factory, YouTubeAnalyticsApiSettings(), clock)
    await analytics.daily_pull(tenant, CTX)
    assert series_pulls(reports) == [f"video=={video_id(1)}", f"video=={RECENT}"]
    rows = (
        await session.execute(
            select(OwnAnalyticsDaily.day, OwnAnalyticsDaily.views)
            .where(OwnAnalyticsDaily.client_id == tenant.client_id, OwnAnalyticsDaily.video_id == RECENT)
            .order_by(OwnAnalyticsDaily.day)
        )
    ).all()
    # the pulled range is complete: the day the api left out is a zero, not a gap
    assert dict(rows)[MISSING_DAY] == 0 and min(day for day, _ in rows) == date(2026, 11, 15)

    reports.queries.clear()
    clock.advance(timedelta(days=1))
    await analytics.daily_pull(tenant, CTX)
    assert series_pulls(reports) == [f"video=={RECENT}"], "only the series short of the horizon is re-pulled"
