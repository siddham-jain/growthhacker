import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.shared import (
    ChannelRepository,
    CommentRepository,
    QuotaLedgerRepository,
    SearchCacheRepository,
    SearchResultItem,
    SnapshotRepository,
    VideoRepository,
    snapshot_slot,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
CHANNEL = "UC0InVdvqNyNzKBl1-TL348A"
VIDEO = "dQw4w9WgXcQ"


def channel_row(title: str, fetched_at: datetime) -> dict:
    return {
        "channel_id": CHANNEL,
        "title": title,
        "metadata_fetched_at": fetched_at,
        "stats_fetched_at": fetched_at,
    }


def video_row(title: str, fetched_at: datetime) -> dict:
    return {
        "video_id": VIDEO,
        "channel_id": CHANNEL,
        "title": title,
        "published_at": NOW - timedelta(days=3),
        "effective_published_at": NOW - timedelta(days=3),
        "ingest_source": "uploads",
        "metadata_fetched_at": fetched_at,
        "stats_fetched_at": fetched_at,
        "view_count": 100,
    }


@pytest.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    await ChannelRepository(session).upsert_many([channel_row("Apollo Hospitals", NOW)])
    await VideoRepository(session).upsert_many([video_row("Knee pain explained", NOW)])
    return session


async def test_channel_upsert_is_monotonic(seeded: AsyncSession) -> None:
    channels = ChannelRepository(seeded)
    await channels.upsert_many([channel_row("Stale title", NOW - timedelta(hours=1))])
    assert (await channels.get(CHANNEL)).title == "Apollo Hospitals"
    await channels.upsert_many([channel_row("Fresh title", NOW + timedelta(hours=1))])
    await seeded.refresh(await channels.get(CHANNEL))
    assert (await channels.get(CHANNEL)).title == "Fresh title"


async def test_video_stats_update_ignores_older_responses(seeded: AsyncSession) -> None:
    videos = VideoRepository(seeded)
    stats = [{"video_id": VIDEO, "view_count": 50, "stats_fetched_at": NOW - timedelta(minutes=5)}]
    await videos.update_stats(stats)
    await videos.update_stats(
        [{"video_id": VIDEO, "view_count": 250, "stats_fetched_at": NOW + timedelta(minutes=5)}]
    )
    video = await videos.get(VIDEO)
    await seeded.refresh(video)
    assert video.view_count == 250


async def test_unknown_columns_and_missing_clocks_are_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="unknown columns"):
        await ChannelRepository(session).upsert_many([{**channel_row("x", NOW), "category_id": "22"}])
    with pytest.raises(ValueError, match="missing required"):
        await ChannelRepository(session).upsert_many([{"channel_id": CHANNEL}])


async def test_mark_missing_keeps_first_missing_time(seeded: AsyncSession) -> None:
    videos = VideoRepository(seeded)
    assert await videos.mark_missing([VIDEO], at=NOW) == 1
    assert await videos.mark_missing([VIDEO], at=NOW + timedelta(hours=6)) == 0
    video = await videos.get(VIDEO)
    await seeded.refresh(video)
    assert (video.availability, video.first_missing_at) == ("missing", NOW)


async def test_snapshots_are_idempotent_and_create_missing_partitions(seeded: AsyncSession) -> None:
    snapshots = SnapshotRepository(seeded)
    far_future = datetime(2027, 3, 1, 10, 15, tzinfo=UTC)
    row = {
        "video_id": VIDEO,
        "slot_at": snapshot_slot(far_future),
        "captured_at": far_future,
        "age_hours": 72.25,
        "view_count": 1_320,
        "source": "batch_stats",
    }
    assert await snapshots.insert_many([row]) == 1
    assert await snapshots.insert_many([row]) == 0
    stored = await snapshots.for_video(VIDEO)
    assert [snapshot.view_count for snapshot in stored] == [1_320]
    assert stored[0].slot_at == datetime(2027, 3, 1, 10, tzinfo=UTC)


async def test_comments_reject_unredacted_length(seeded: AsyncSession) -> None:
    comment = {
        "comment_id": "Ugx1",
        "video_id": VIDEO,
        "thread_id": "Ugx1",
        "is_reply": False,
        "text_redacted": "how much does it cost in [city]?",
        "published_at": NOW,
        "fetched_at": NOW,
    }
    assert await CommentRepository(seeded).upsert_many([comment]) == 1
    with pytest.raises(ValueError, match="1500"):
        await CommentRepository(seeded).upsert_many([{**comment, "text_redacted": "x" * 1501}])


async def test_search_cache_round_trip_and_ttl_cap(seeded: AsyncSession) -> None:
    cache = SearchCacheRepository(seeded)
    items = [SearchResultItem("video", VIDEO, CHANNEL), SearchResultItem("channel", CHANNEL)]
    stored = await cache.put(
        query_key="q:knee",
        endpoint="search.list",
        params={"q": "knee pain"},
        items=items,
        fetched_at=NOW,
        ttl=timedelta(hours=72),
        quota_units=1,
    )
    assert stored
    hit = await cache.get_fresh("q:knee", now=NOW + timedelta(hours=1))
    assert hit is not None and [item.resource_id for item in hit.items] == [VIDEO, CHANNEL]
    assert await cache.get_fresh("q:knee", now=NOW + timedelta(hours=73)) is None
    older = await cache.put(
        query_key="q:knee",
        endpoint="search.list",
        params={},
        items=[],
        fetched_at=NOW - timedelta(hours=1),
        ttl=timedelta(hours=1),
        quota_units=1,
    )
    assert not older
    with pytest.raises(ValueError):
        await cache.put(
            query_key="q:long",
            endpoint="search.list",
            params={},
            items=[],
            fetched_at=NOW,
            ttl=timedelta(days=29),
            quota_units=1,
        )


async def test_quota_ledger_is_idempotent_per_request(session: AsyncSession) -> None:
    ledger = QuotaLedgerRepository(session)
    request_id = uuid.uuid4()
    entry = {
        "request_id": request_id,
        "occurred_at": NOW,
        "pt_day": NOW.date(),
        "bucket": "search",
        "endpoint": "search.list",
        "units": 1,
        "scope": "onboarding",
    }
    assert await ledger.record([entry]) == 1
    assert await ledger.record([entry]) == 0
    await ledger.record([{**entry, "request_id": uuid.uuid4(), "bucket": "main", "units": 3}])
    assert await ledger.units_by_bucket(NOW.date()) == {"search": 1, "main": 3}
