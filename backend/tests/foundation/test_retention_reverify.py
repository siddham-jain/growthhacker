from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import FrozenClock
from app.core.config import PolicySettings, RetentionSettings
from app.core.db import SessionFactory
from app.models.competitors import CompetitorDriftEvent, CompetitorSet
from app.models.topics import TopicMatchOverride
from app.models.youtube_cache import YtChannel, YtVideo
from app.retention.rules import retention_registry
from app.retention.sweeper import RetentionSweeper
from tests.foundation.factories import create_tenant, insert_row
from tests.foundation.test_retention_sweeper import RecordingRefresher

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
LIVE = "UC" + "l" * 22
LAPSED = "UC" + "m" * 22
GONE = "UC" + "n" * 22


def age(days: float) -> datetime:
    return NOW - timedelta(days=days)


def sweeper(session_factory: SessionFactory, refresher=None) -> RetentionSweeper:
    return RetentionSweeper(
        session_factory, FrozenClock(NOW), PolicySettings(), RetentionSettings(), refresher=refresher
    )


async def test_drift_events_quoting_api_data_are_deleted_after_28_days(
    session: AsyncSession, session_factory: SessionFactory
) -> None:
    scope = await create_tenant(session, "apollo")
    old = await insert_row(session, CompetitorDriftEvent.__table__, scope, {"detected_at": age(29)})
    new = await insert_row(session, CompetitorDriftEvent.__table__, scope, {"detected_at": age(2)})
    await session.commit()
    await sweeper(session_factory).sweep([retention_registry.get("competitor_drift_events")])
    remaining = set((await session.execute(select(CompetitorDriftEvent.id))).scalars())
    assert old["id"] not in remaining and new["id"] in remaining


async def test_reverification_refreshes_live_ids_and_redacts_gone_ones(
    session: AsyncSession, session_factory: SessionFactory
) -> None:
    scope = await create_tenant(session, "apollo")
    session.add(YtChannel(channel_id=LIVE, metadata_fetched_at=age(1), stats_fetched_at=age(1)))
    session.add(YtChannel(channel_id=LAPSED, metadata_fetched_at=age(28.5), stats_fetched_at=age(28.5)))
    await session.flush()
    session.add(
        YtVideo(
            video_id="livevideo01",
            channel_id=LIVE,
            published_at=age(60),
            effective_published_at=age(60),
            ingest_source="uploads",
            metadata_fetched_at=age(1),
            stats_fetched_at=age(1),
        )
    )
    await session.flush()
    verified = await insert_row(
        session,
        CompetitorSet.__table__,
        scope,
        {"competitor_channel_ids": [LIVE], "ids_verified_at": age(28.5)},
    )
    pending = await insert_row(
        session,
        CompetitorSet.__table__,
        scope,
        {"version": 2, "competitor_channel_ids": [LIVE, LAPSED], "ids_verified_at": age(28.5)},
    )
    expired = await insert_row(
        session,
        CompetitorSet.__table__,
        scope,
        {
            "version": 3,
            "competitor_channel_ids": [LIVE, GONE],
            "niche_channel_ids": [GONE],
            "ids_verified_at": age(29.5),
        },
    )
    live_override = await insert_row(
        session,
        TopicMatchOverride.__table__,
        scope,
        {"video_id": "livevideo01", "ids_verified_at": age(28.5)},
    )
    gone_override = await insert_row(
        session,
        TopicMatchOverride.__table__,
        scope,
        {"video_id": "gonevideo01", "topic_ref_id": "t2", "ids_verified_at": age(29.5)},
    )
    await session.commit()

    refresher = RecordingRefresher()
    rules = [retention_registry.get(name) for name in ("competitor_sets", "topic_match_overrides")]
    report = await sweeper(session_factory, refresher).sweep(rules)

    sets = {
        row.id: row
        for row in (
            await session.execute(select(CompetitorSet).execution_options(populate_existing=True))
        ).scalars()
    }
    assert sets[verified["id"]].ids_verified_at == NOW
    assert sets[pending["id"]].competitor_channel_ids == [LIVE, LAPSED], "kept until its hard limit"
    assert ("yt_channels", [LAPSED]) in refresher.calls
    assert (
        sets[expired["id"]].competitor_channel_ids == [LIVE] and sets[expired["id"]].niche_channel_ids == []
    )
    assert sets[expired["id"]].ids_verified_at == NOW
    overrides = set((await session.execute(select(TopicMatchOverride.id))).scalars())
    assert live_override["id"] in overrides and gone_override["id"] not in overrides
    assert report.per_rule["competitor_sets"].redacted == 1
    assert report.violations == []
