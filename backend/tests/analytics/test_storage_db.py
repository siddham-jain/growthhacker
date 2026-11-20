from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.youtube_derived import YtChannelBaseline, YtVideoMetric, YtVideoOutlier, YtVideoTrait
from app.repositories.shared import ChannelRepository, CommentRepository, SnapshotRepository, VideoRepository
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.inputs import AnalysisRequest
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.storage import AnalyticsStore, SharedCacheInputSource
from tests.analytics.factories import AS_OF, CLIENT_ID, channel_id, make_video, video_id

pytestmark = pytest.mark.db

CHANNEL = channel_id(42)
OTHER = channel_id(43)


def _video_row(n: int, channel: str, views: int) -> dict:
    published = AS_OF - timedelta(days=3 + n * 2)
    video = make_video(
        video_id("db", n if channel == CHANNEL else 100 + n),
        channel,
        published,
        title=f"Topic {n} explained in full detail",
        views=views,
        duration_s=400 + n * 9,
        description="Book now: https://example.org/book",
    )
    row = video.model_dump(
        exclude={"aspect", "format_confidence", "localizations", "region_restriction", "thumbnails"}
    )
    return row | {
        "format": video.format.value,
        "format_confidence": 0.9,
        "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{video.video_id}/hqdefault.jpg"}},
        "stats_fetched_at": AS_OF - timedelta(hours=1),
    }


async def _seed(session) -> None:
    await ChannelRepository(session).upsert_many(
        [
            {"channel_id": cid, "title": title, "metadata_fetched_at": AS_OF, "stats_fetched_at": AS_OF}
            for cid, title in ((CHANNEL, "Own Clinic"), (OTHER, "Rival Clinic"))
        ]
    )
    rows = [_video_row(n, CHANNEL, 1_000 + n * 10) for n in range(12)]
    rows += [_video_row(n, OTHER, 9_000 if n == 4 else 800 + n * 15) for n in range(12)]
    rows = [
        {k: v for k, v in row.items() if k not in ("live_broadcast_content",) or v is not None}
        for row in rows
    ]
    await VideoRepository(session).upsert_many(rows)
    snapshots = []
    for row in rows:
        for age in (1, 3, 7):
            captured = row["published_at"] + timedelta(days=age)
            if captured < AS_OF:
                snapshots.append(
                    {
                        "video_id": row["video_id"],
                        "slot_at": captured.replace(minute=0, second=0, microsecond=0),
                        "captured_at": captured,
                        "age_hours": age * 24.0,
                        "view_count": int(row["view_count"] * age / 7),
                        "like_count": int(row["view_count"] * age / 7 * 0.02),
                        "comment_count": 1,
                        "source": "batch_stats",
                    }
                )
    await SnapshotRepository(session).insert_many(snapshots)
    await CommentRepository(session).upsert_many(
        [
            {
                "comment_id": f"cm{n}",
                "video_id": rows[0]["video_id"],
                "thread_id": f"th{n}",
                "is_reply": False,
                "text_redacted": "How much does the treatment cost?",
                "published_at": AS_OF - timedelta(days=1),
                "fetched_at": AS_OF - timedelta(hours=2),
            }
            for n in range(3)
        ]
    )


async def test_shared_cache_round_trip(session_factory):
    async with session_factory() as session, session.begin():
        await _seed(session)
    request = AnalysisRequest(
        workspace_id=CLIENT_ID,
        client_id=CLIENT_ID,
        as_of=AS_OF,
        own_channel_id=CHANNEL,
        competitor_channel_ids=[OTHER],
        timezone="Asia/Kolkata",
        country="IN",
    )
    inputs = await SharedCacheInputSource(session_factory).load(request)
    assert {c.channel.channel_id for c in inputs.channels} == {CHANNEL, OTHER}
    assert len(inputs.videos) == 24
    assert len(inputs.snapshots) == 24 and all(items for items in inputs.snapshots.values())
    assert len(inputs.comments[video_id("db", 0)]) == 3

    result = AnalyticsEngine(AnalyticsEngineSettings()).run(inputs, AS_OF)
    written = await AnalyticsStore(session_factory).write(result, inputs)
    assert written["yt_video_metrics"] == 24

    async with session_factory() as session:
        metrics = (await session.execute(select(YtVideoMetric))).scalars().all()
        outliers = (await session.execute(select(YtVideoOutlier))).scalars().all()
        baselines = (await session.execute(select(YtChannelBaseline))).scalars().all()
        traits = (await session.execute(select(YtVideoTrait))).scalars().all()
    assert {m.channel_id for m in metrics} == {CHANNEL, OTHER}
    assert all(m.source_oldest_at <= AS_OF for m in metrics)
    assert outliers and baselines and len(traits) == 24
    spike = next(o for o in outliers if o.video_id == video_id("db", 104) and o.age_days == 7)
    assert spike.is_outlier and spike.method in ("observed", "interpolated")

    again = await AnalyticsStore(session_factory).write(result, inputs)
    assert again == written


async def test_recurring_text_marker_and_thumbnail_rows(session_factory):
    async with session_factory() as session, session.begin():
        await _seed(session)
    store = AnalyticsStore(session_factory)
    assert await store.recurring_text(CHANNEL, AS_OF - timedelta(days=7)) is None
    await store.write_recurring_text(CHANNEL, {}, AS_OF)
    assert await store.recurring_text(CHANNEL, AS_OF - timedelta(days=7)) == frozenset()
    await store.write_recurring_text(CHANNEL, {("apollo", 8): 0.6}, AS_OF)
    assert await store.recurring_text(CHANNEL, AS_OF - timedelta(days=7)) == frozenset({("apollo", 8)})
