from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import FrozenClock
from app.core.config import PolicySettings, RetentionSettings
from app.core.db import SessionFactory
from app.models.recommendations import Recommendation
from app.models.youtube_cache import YtChannel, YtComment, YtVideo
from app.retention.partitions import DailyPartition, existing_partitions
from app.retention.rules import retention_registry
from app.retention.sweeper import RetentionSweeper
from tests.foundation.factories import create_tenant, insert_row

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
OWN = "UC" + "a" * 22
UNREFERENCED = "UC" + "b" * 22
REFERENCED_BUT_EXPIRED = "UC" + "c" * 22
FRESH = "UC" + "d" * 22


class RecordingRefresher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def schedule(self, table: str, ids: Sequence[str]) -> None:
        self.calls.append((table, list(ids)))


def age(days: float) -> datetime:
    return NOW - timedelta(days=days)


async def seed_cache(session: AsyncSession) -> None:
    channels = [(OWN, 27.9), (UNREFERENCED, 27.9), (REFERENCED_BUT_EXPIRED, 29.5), (FRESH, 1)]
    for channel_id, days in channels:
        session.add(
            YtChannel(channel_id=channel_id, metadata_fetched_at=age(days), stats_fetched_at=age(days))
        )
    await session.flush()
    session.add(
        YtVideo(
            video_id="ownvideo001",
            channel_id=OWN,
            published_at=age(60),
            effective_published_at=age(60),
            ingest_source="uploads",
            metadata_fetched_at=age(27.9),
            stats_fetched_at=age(27.9),
        )
    )
    await session.flush()
    for comment_id, days in (("old", 29), ("new", 1)):
        session.add(
            YtComment(
                comment_id=comment_id,
                video_id="ownvideo001",
                thread_id=comment_id,
                is_reply=False,
                text_redacted="is this covered by insurance?",
                published_at=age(days),
                fetched_at=age(days),
            )
        )
    await session.flush()


async def test_full_sweep_enforces_policy(session: AsyncSession, session_factory: SessionFactory) -> None:
    scope = await create_tenant(session, "apollo", own_channel_id=OWN, own_channel_status="verified")
    await create_tenant(
        session, "fortis", own_channel_id=REFERENCED_BUT_EXPIRED, own_channel_status="verified"
    )
    await seed_cache(session)
    stale_card = await insert_row(session, Recommendation.__table__, scope, {"api_data_observed_at": age(29)})
    expired_old = DailyPartition("yt_video_snapshots", (NOW - timedelta(days=40)).date())
    await session.execute(text(expired_old.create_sql()))
    await session.commit()

    refresher = RecordingRefresher()
    sweeper = RetentionSweeper(
        session_factory, FrozenClock(NOW), PolicySettings(), RetentionSettings(), refresher=refresher
    )
    report = await sweeper.sweep()

    remaining = set((await session.execute(select(YtChannel.channel_id))).scalars())
    assert remaining == {OWN, FRESH}, "unreferenced rows go at 28d; referenced rows past 29d fail closed"
    assert ("yt_channels", [OWN]) in refresher.calls
    assert ("yt_videos", ["ownvideo001"]) in refresher.calls
    assert set((await session.execute(select(YtComment.comment_id))).scalars()) == {"new"}

    recommendation = (
        await session.execute(select(Recommendation).where(Recommendation.id == stale_card["id"]))
    ).scalar_one()
    await session.refresh(recommendation)
    assert recommendation.status == "expired"
    assert recommendation.payload is None and recommendation.redacted_at is not None

    names = {partition.name for partition in await existing_partitions(session, "yt_video_snapshots")}
    assert expired_old.name not in names
    assert DailyPartition("yt_video_snapshots", NOW.date() + timedelta(days=7)).name in names
    assert report.violations == []
    assert set(report.per_rule) == set(retention_registry.keys())


async def test_audit_flags_rows_past_the_deadline(
    session: AsyncSession, session_factory: SessionFactory
) -> None:
    session.add(YtChannel(channel_id=OWN, metadata_fetched_at=age(31), stats_fetched_at=age(31)))
    await session.commit()
    sweeper = RetentionSweeper(session_factory, FrozenClock(NOW), PolicySettings(), RetentionSettings())
    _, violations = await sweeper.audit([retention_registry.get("yt_channels")])
    assert violations and violations[0].startswith("yt_channels")


def test_derived_metrics_mode_extends_only_stats_rules() -> None:
    approved = PolicySettings(retention_mode="derived_metrics_36m")
    snapshots = retention_registry.get("yt_video_snapshots")
    comments = retention_registry.get("yt_comments")
    assert snapshots.max_age(approved) == timedelta(days=30 * 36)
    assert comments.max_age(approved) == timedelta(days=28)
    assert snapshots.max_age(PolicySettings()) == timedelta(days=28)
