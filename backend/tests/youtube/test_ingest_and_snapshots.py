from datetime import timedelta

import pytest

from app.core.config import YouTubeFreshnessSettings, YouTubeIngestSettings, YouTubeSnapshotSettings
from app.domain.common import ChannelRole
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.ports.quota import QuotaExhausted
from app.ports.youtube import PlaylistItem
from app.services.youtube.context import request_context
from app.services.youtube.ingest import IngestService
from app.services.youtube.snapshots import (
    CadencePlanner,
    SnapshotCollector,
    SnapshotReader,
    SnapshotScheduler,
)
from tests.youtube.fixtures import CHANNEL_A, NOW, FakeGateway, channel_record, video_id, video_record

pytestmark = pytest.mark.db

CTX = request_context(QuotaPriority.interactive)
PLAYLIST = "UU" + CHANNEL_A[2:]
SHORT = {"duration": "PT40S", "width": 400, "height": 711}
LONG = {"duration": "PT9M", "width": 400, "height": 225}


def uploads(count: int, *, every_h: float, start_index: int = 0, shape=lambda i: SHORT if i % 2 else LONG):
    items, videos = [], []
    for i in range(start_index, start_index + count):
        published = NOW - timedelta(hours=every_h * (i - start_index) + 1)
        items.append(PlaylistItem(video_id=video_id(i), published_at=published))
        videos.append(video_record(video_id(i), channel_id=CHANNEL_A, published=published, **shape(i)))
    return items, videos


def build(clock, store, *, count: int = 120, every_h: float = 45, shape=None):
    items, videos = uploads(count, every_h=every_h, **({"shape": shape} if shape else {}))
    gateway = PersistingGateway(
        clock,
        channels=[channel_record(CHANNEL_A, handle="@testchannela")],
        videos=videos,
        uploads={PLAYLIST: items},
    )
    gateway.store = store
    planner = CadencePlanner(YouTubeSnapshotSettings(), horizon_days=180)
    scheduler = SnapshotScheduler(store, planner, clock)
    ingest = IngestService(
        gateway, store, scheduler, YouTubeIngestSettings(), YouTubeFreshnessSettings(), clock
    )
    return gateway, ingest, scheduler


class PersistingGateway(FakeGateway):
    """writes hydrated videos through to the store, as the cached gateway does."""

    store = None

    async def videos_by_id(self, ids, ctx, with_localizations=False):
        batch = await super().videos_by_id(ids, ctx, with_localizations)
        await self.store.save_videos(batch.found)
        return batch


async def persist(store, gateway: FakeGateway) -> None:
    await store.save_channels(list(gateway.channels.values()))


async def test_full_ingest_reads_two_pages_for_an_apollo_like_channel(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=400)
    await persist(store, gateway)
    result = await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    assert result.status == "ok"
    assert result.pages == 2 and len(result.videos) == 100
    assert gateway.calls.count("playlistItems.list") == 2
    channel = (await store.channels([CHANNEL_A]))[CHANNEL_A]
    assert channel.uploads_synced_at == NOW and channel.uploads_90d > 0
    assert len(channel.recent_upload_ids) == 50 and channel.cadence_flags == []


async def test_incremental_ingest_uses_the_etag_and_stops_at_known_videos(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=60)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX)
    clock.advance(timedelta(hours=7))
    calls = gateway.calls.count("playlistItems.list")
    unchanged = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX, mode="incremental")
    assert unchanged.new_video_ids == [] and gateway.calls.count("playlistItems.list") == calls + 1
    assert unchanged.stats_refreshed > 0
    new_items, new_videos = uploads(3, every_h=1, start_index=500)
    gateway.uploads[PLAYLIST] = new_items + gateway.uploads[PLAYLIST]
    gateway.videos.update({video.video_id: video for video in new_videos})
    clock.advance(timedelta(hours=7))
    fresh = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX, mode="incremental")
    assert set(fresh.new_video_ids) == {video.video_id for video in new_videos}


async def test_incremental_skips_uploads_inside_the_freshness_window(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=10)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    calls = gateway.calls.count("playlistItems.list")
    clock.advance(timedelta(hours=1))
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX, mode="incremental")
    assert gateway.calls.count("playlistItems.list") == calls


async def test_invalid_page_token_restarts_once_and_dedupes(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=120)
    await persist(store, gateway)
    gateway.invalid_tokens.add("50")
    result = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX)
    ids = [video.video_id for video in result.videos]
    assert len(ids) == len(set(ids)) == 100


async def test_channel_without_uploads_and_missing_playlist(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=0)
    gateway.channels[CHANNEL_A] = channel_record(CHANNEL_A, uploads=False)
    await persist(store, gateway)
    result = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX)
    assert "very_new_channel" in result.flags and result.message == "This channel has no public videos yet."
    gateway.channels[CHANNEL_A] = channel_record(CHANNEL_A)
    gateway.uploads.clear()
    result = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX)
    assert result.status == "ok" and result.videos == [] and "very_new_channel" in result.flags


async def test_unknown_channel_and_quota_exhaustion(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=10)
    gone = await ingest.ingest_channel("UCtestGone000000000000GG", ChannelRole.competitor, CTX)
    assert gone.status == "not_found"

    async def exhausted(*args, **kwargs):
        raise QuotaExhausted(QuotaBucket.main, NOW + timedelta(hours=3))

    gateway.uploads_page = exhausted
    await persist(store, gateway)
    limited = await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    assert limited.status == "quota_limited" and limited.quota_bucket is QuotaBucket.main


async def test_shorts_only_flag_and_duplicate_titles(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=8, every_h=24, shape=lambda i: SHORT)
    first, second = sorted(gateway.videos.values(), key=lambda v: v.published_at)[:2]
    gateway.videos[second.video_id] = second.model_copy(update={"title": first.title})
    await persist(store, gateway)
    result = await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    assert "shorts_only" in result.flags and "Shorts" in result.message
    rows = await store.videos([first.video_id, second.video_id])
    assert rows[second.video_id].quality_flags == ["duplicate_title_in_channel"]
    assert rows[first.video_id].quality_flags == []


async def test_ingested_videos_are_scheduled_and_collected(clock, store) -> None:
    gateway, ingest, scheduler = build(clock, store, count=5, every_h=10)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    clock.advance(timedelta(hours=1, minutes=5))
    due = await scheduler.due_batch(100)
    assert {video.video_id for video in due} == set(gateway.videos)
    collector = SnapshotCollector(gateway, scheduler, YouTubeSnapshotSettings(), clock)
    report = await collector.collect()
    assert report.captured == 5 and gateway.calls.count("videos.batchGetStats") == 1
    assert await scheduler.due_batch(100) == []


async def test_collector_falls_back_to_videos_list_near_milestones(clock, store) -> None:
    gateway, ingest, scheduler = build(clock, store, count=2, every_h=20)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    clock.advance(timedelta(hours=2))
    gateway.fail_stats = QuotaExhausted(QuotaBucket.batch_stats, NOW + timedelta(hours=5))
    report = await SnapshotCollector(gateway, scheduler, YouTubeSnapshotSettings(), clock).collect()
    assert report.quota_limited and report.fallback == 1
    assert gateway.calls.count("videos.list") >= 1


async def test_snapshot_reader_returns_raw_series(clock, store) -> None:
    gateway, ingest, _ = build(clock, store, count=1)
    await persist(store, gateway)
    video = next(iter(gateway.videos.values()))
    await store.save_videos([video])
    await store.save_stats((await gateway.video_stats([video.video_id], CTX)).snapshots)
    series = await SnapshotReader(store).snapshots(video.video_id)
    assert [snapshot.source for snapshot in series] == ["batch_stats"]
    assert series[0].counting_regime == "2026-11-16-coviews"
