import asyncio
import uuid
from datetime import timedelta

import pytest
from redis.asyncio import Redis

from app.domain.youtube import QuotaPriority, SearchHit, SearchPage, SearchQuery
from app.services.youtube.context import request_context
from app.services.youtube.single_flight import BatchCoalescer
from tests.youtube.fixtures import (
    CHANNEL_A,
    CHANNEL_B,
    NOW,
    FakeGateway,
    channel_record,
    video_id,
    video_record,
)

pytestmark = pytest.mark.db

CLIENT = uuid.uuid4()
CTX = request_context(QuotaPriority.interactive, client_id=CLIENT)


def inner(clock, videos: int = 3) -> FakeGateway:
    return FakeGateway(
        clock,
        channels=[
            channel_record(CHANNEL_A, handle="@testchannela"),
            channel_record(CHANNEL_B, handle="@testb"),
        ],
        videos=[video_record(video_id(i), channel_id=CHANNEL_A) for i in range(videos)],
        handles={"testchannela": CHANNEL_A},
    )


async def test_cache_hits_never_reach_upstream(make_cached, clock, redis: Redis, redis_prefix: str) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    ids = [video_id(i) for i in range(3)]
    first = await gateway.videos_by_id(ids, CTX)
    calls = list(upstream.calls)
    assert calls == ["videos.list", "channels.list"]
    second = await gateway.videos_by_id(ids, CTX)
    assert upstream.calls == calls
    assert [v.video_id for v in first.found] == [v.video_id for v in second.found] == ids
    assert second.found[0].format == first.found[0].format
    assert second.found[0].thumbnails == first.found[0].thumbnails
    hits = await redis.hgetall(f"{redis_prefix}:yt_cache_hits:2026-11-20")
    assert hits == {f"{CLIENT}|video_metadata": "3"}


async def test_stale_rows_and_max_age_override_refetch(make_cached, clock) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    await gateway.channels_by_id([CHANNEL_A], CTX)
    await gateway.channels_by_id([CHANNEL_A], CTX)
    assert upstream.calls.count("channels.list") == 1
    await gateway.channels_by_id([CHANNEL_A], request_context(QuotaPriority.interactive, max_age_s=0))
    assert upstream.calls.count("channels.list") == 2
    clock.advance(timedelta(hours=25))
    upstream.channels[CHANNEL_A] = channel_record(CHANNEL_A, fetched_at=clock.now())
    await gateway.channels_by_id([CHANNEL_A], CTX)
    assert upstream.calls.count("channels.list") == 3


async def test_hydration_writes_videos_and_a_snapshot(make_cached, clock, store) -> None:
    gateway = make_cached(inner(clock))
    await gateway.videos_by_id([video_id(0)], CTX)
    rows = await store.videos([video_id(0)])
    assert rows[video_id(0)].channel_id == CHANNEL_A and rows[video_id(0)].ingest_source == "manual"
    snapshots = await store.snapshots([video_id(0)])
    assert [s.source for s in snapshots[video_id(0)]] == ["videos_list"]


async def test_missing_then_removed_after_second_miss(make_cached, clock, store) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    await gateway.videos_by_id([video_id(0)], CTX)
    upstream.videos.pop(video_id(0))
    fresh = request_context(QuotaPriority.interactive, max_age_s=0)
    batch = await gateway.videos_by_id([video_id(0)], fresh)
    assert batch.missing_ids == [video_id(0)]
    assert (await store.videos([video_id(0)]))[video_id(0)].availability == "missing"
    clock.advance(timedelta(hours=7))
    await gateway.videos_by_id([video_id(0)], fresh)
    assert (await store.videos([video_id(0)]))[video_id(0)].availability == "removed"
    calls = upstream.calls.count("videos.list")
    await gateway.videos_by_id([video_id(0)], fresh)
    assert upstream.calls.count("videos.list") == calls


async def test_reappearing_video_is_available_again(make_cached, clock, store) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    await gateway.videos_by_id([video_id(0)], CTX)
    kept = upstream.videos.pop(video_id(0))
    fresh = request_context(QuotaPriority.interactive, max_age_s=0)
    await gateway.videos_by_id([video_id(0)], fresh)
    upstream.videos[video_id(0)] = kept.model_copy(update={"metadata_fetched_at": clock.now()})
    await gateway.videos_by_id([video_id(0)], fresh)
    row = (await store.videos([video_id(0)]))[video_id(0)]
    assert row.availability == "available" and row.first_missing_at is None


async def test_handle_aliases_are_cached_including_not_found(make_cached, clock) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    assert (await gateway.channel_by_handle("@TestChannelA", CTX)).channel_id == CHANNEL_A
    assert (await gateway.channel_by_handle("testchannela", CTX)).channel_id == CHANNEL_A
    assert await gateway.channel_by_handle("@nobody", CTX) is None
    assert await gateway.channel_by_handle("@nobody", CTX) is None
    assert upstream.calls.count("channels.list:forHandle") == 2


async def test_search_pages_are_shared_by_key(make_cached, clock, committed_store) -> None:
    upstream = inner(clock)
    upstream.search_pages["zz-test knee replacement"] = SearchPage(
        query_key="ignored",
        items=[SearchHit(kind="video", id=video_id(1), channel_id=CHANNEL_A, rank=0)],
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=12),
        from_cache=False,
    )
    gateway = make_cached(upstream, cache_store=committed_store)
    query = SearchQuery(q="zz-test knee replacement", type="video")
    other_client = request_context(QuotaPriority.daily_refresh, client_id=uuid.uuid4())
    first, second = await asyncio.gather(
        gateway.search(query, None, CTX), gateway.search(query, None, other_client)
    )
    assert upstream.calls.count("search.list") == 1
    assert {first.from_cache, second.from_cache} == {False, True}
    assert second.items[0].id == video_id(1) or first.items[0].id == video_id(1)


async def test_empty_scraped_search_is_not_cached(make_cached, clock, committed_store) -> None:
    upstream = inner(clock)
    upstream.search_pages["zz-test empty scrape"] = SearchPage(
        query_key="ignored", items=[], fetched_at=NOW, expires_at=NOW, from_cache=False
    )
    gateway = make_cached(upstream, cache_store=committed_store)
    query = SearchQuery(q="zz-test empty scrape", type="video")
    await gateway.search(query, None, CTX)
    again = await gateway.search(query, None, CTX)
    assert upstream.calls.count("search.list") == 2
    assert not again.from_cache


async def test_video_stats_write_through_with_live_age_alignment(make_cached, clock, store) -> None:
    live = video_record(
        video_id(9),
        channel_id=CHANNEL_A,
        duration="PT1H",
        width=400,
        height=225,
        published=NOW - timedelta(days=3),
        live_details={"actualStartTime": "2026-11-19T09:30:00Z", "actualEndTime": "2026-11-19T10:30:00Z"},
    )
    upstream = inner(clock)
    upstream.videos[live.video_id] = live
    gateway = make_cached(upstream)
    await gateway.videos_by_id([live.video_id], CTX)
    clock.advance(timedelta(hours=2))
    batch = await gateway.video_stats([live.video_id, video_id(77)], CTX)
    assert batch.missing_ids == [video_id(77)]
    assert batch.snapshots[0].age_hours == pytest.approx(26.0)
    assert len((await store.snapshots([live.video_id]))[live.video_id]) == 2


async def test_coalescer_merges_concurrent_callers_into_fifty_id_calls() -> None:
    calls: list[int] = []

    async def fetch(ids, context):
        calls.append(len(ids))
        return {i: i for i in ids}

    coalescer = BatchCoalescer(fetch, max_batch=50, window_s=0.05)
    groups = [[video_id(i) for i in range(start, start + 40)] for start in (0, 40, 80)]
    results = await asyncio.gather(*(coalescer.get(group, "ctx") for group in groups))
    assert sorted(calls) == [30, 40, 50]
    assert all(set(result) == set(group) for result, group in zip(results, groups, strict=True))


async def test_two_runs_for_the_same_videos_make_one_upstream_call(
    make_cached, clock, committed_store
) -> None:
    upstream = inner(clock, videos=10)
    gateway = make_cached(upstream, cache_store=committed_store)
    ids = [video_id(i) for i in range(10)]
    run_a = request_context(QuotaPriority.interactive, run_id=uuid.uuid4())
    run_b = request_context(QuotaPriority.interactive, run_id=uuid.uuid4())
    a, b = await asyncio.gather(gateway.videos_by_id(ids, run_a), gateway.videos_by_id(ids, run_b))
    assert upstream.calls.count("videos.list") == 1
    assert len(a.found) == len(b.found) == 10


async def test_upcoming_videos_are_rechecked_after_their_start(make_cached, clock, store) -> None:
    upcoming = video_record(
        video_id(5),
        channel_id=CHANNEL_A,
        duration="P0D",
        width=None,
        height=None,
        live="upcoming",
        live_details={"scheduledStartTime": "2026-11-20T12:00:00Z"},
    )
    upstream = inner(clock)
    upstream.videos[upcoming.video_id] = upcoming
    gateway = make_cached(upstream)
    await gateway.videos_by_id([upcoming.video_id], CTX)
    await gateway.videos_by_id([upcoming.video_id], CTX)
    assert upstream.calls.count("videos.list") == 1
    clock.advance(timedelta(hours=3))
    await gateway.videos_by_id([upcoming.video_id], CTX)
    assert upstream.calls.count("videos.list") == 2


async def test_view_count_drops_are_flagged_not_hidden(make_cached, clock, store) -> None:
    upstream = inner(clock)
    gateway = make_cached(upstream)
    await gateway.videos_by_id([video_id(0)], CTX)
    upstream.videos[video_id(0)] = upstream.videos[video_id(0)].model_copy(update={"view_count": 900})
    clock.advance(timedelta(hours=2))
    batch = await gateway.video_stats([video_id(0)], CTX)
    assert batch.snapshots[0].view_count == 900
    assert (await store.videos([video_id(0)]))[video_id(0)].quality_flags == ["count_decreased"]
