import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.adapters.youtube.errors import InvalidRequest, Transient
from app.adapters.youtube.middleware import MeteredHandler
from app.core.config import YouTubeFreshnessSettings, YouTubeIngestSettings, YouTubeSnapshotSettings
from app.domain.common import ChannelRole
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.models.youtube_cache import YtSnapshotSchedule
from app.ports.quota import QuotaExhausted
from app.services.youtube.context import request_context
from app.services.youtube.ingest import IngestService
from app.services.youtube.ledger import QuotaLedger
from app.services.youtube.single_flight import BatchCoalescer
from app.services.youtube.snapshots import CadencePlanner, SnapshotCollector, SnapshotScheduler
from tests.youtube.fixtures import CHANNEL_A, NOW, FakeGateway, channel_record, video_id, video_record
from tests.youtube.test_ingest_and_snapshots import (
    CTX,
    PLAYLIST,
    PersistingGateway,
    build,
    persist,
    uploads,
)

pytestmark = pytest.mark.db

CLIENT_A, CLIENT_B = uuid.uuid4(), uuid.uuid4()


class BrokenUow:
    async def __aenter__(self):
        raise ConnectionError("db down")

    async def __aexit__(self, *exc) -> bool:
        return False


class AlwaysOk:
    calls = 0

    async def send(self, request):
        self.calls += 1
        return type("Response", (), {"status": 200})()


class FreeQuota:
    async def reserve(self, *args):
        return object()

    async def refund(self, *args) -> None:
        return None

    async def mark_exhausted(self, bucket):
        return NOW


class Request:
    endpoint = "videos.list"
    cache_outcome = "miss"
    ctx = request_context(QuotaPriority.interactive)


class FakeClock:
    def now(self) -> datetime:
        return NOW


async def test_a_failed_ledger_flush_never_fails_the_calls_after_it() -> None:
    inner = AlwaysOk()
    ledger = QuotaLedger(lambda: BrokenUow(), FakeClock(), flush_at=2)
    handler = MeteredHandler(inner, FreeQuota(), ledger)
    responses = [await handler.send(Request()) for _ in range(5)]
    assert [response.status for response in responses] == [200] * 5 and inner.calls == 5
    assert len(ledger._buffer) == 5, "rows wait for the next flush instead of being dropped"
    assert await ledger.flush() == 0


async def test_coalesced_callers_pay_for_their_own_batches() -> None:
    calls: list[tuple[str, list[str]]] = []

    async def fetch(ids, context):
        calls.append((context, ids))
        if context == "client-A":
            raise QuotaExhausted(QuotaBucket.main, datetime.now(UTC))
        return {i: f"video {i}" for i in ids}

    coalescer = BatchCoalescer(fetch, window_s=0.02)
    a, b = await asyncio.gather(
        coalescer.get(["v1"], "client-A"), coalescer.get(["v2"], "client-B"), return_exceptions=True
    )
    assert isinstance(a, QuotaExhausted)
    assert b == {"v2": "video v2"}
    assert sorted(calls) == [("client-A", ["v1"]), ("client-B", ["v2"])]


class CappedGateway(FakeGateway):
    """client A has hit its own cap; nobody else has."""

    async def videos_by_id(self, ids, ctx, with_localizations=False):
        if ctx.client_id == CLIENT_A:
            await asyncio.sleep(0.05)
            raise QuotaExhausted(QuotaBucket.main, NOW + timedelta(hours=1))
        return await super().videos_by_id(ids, ctx, with_localizations)


async def test_one_clients_cap_does_not_fail_another_waiting_on_the_same_videos(
    make_cached, clock, committed_store
) -> None:
    ids = [video_id(i) for i in range(3)]
    upstream = CappedGateway(
        clock,
        channels=[channel_record(CHANNEL_A)],
        videos=[video_record(i, channel_id=CHANNEL_A) for i in ids],
    )
    gateway = make_cached(upstream, cache_store=committed_store)
    a_ctx = request_context(QuotaPriority.interactive, client_id=CLIENT_A)
    b_ctx = request_context(QuotaPriority.interactive, client_id=CLIENT_B)
    a = asyncio.create_task(gateway.videos_by_id(ids, a_ctx))
    # b asks while a holds the in-flight claim on the same ids
    await asyncio.sleep(0.03)
    b = await gateway.videos_by_id(ids, b_ctx)
    with pytest.raises(QuotaExhausted):
        await a
    assert [video.video_id for video in b.found] == ids


async def test_an_empty_localization_result_is_cached(make_cached, clock) -> None:
    foreign = video_record(video_id(7), channel_id=CHANNEL_A, default_language="ta")
    upstream = FakeGateway(clock, channels=[channel_record(CHANNEL_A)], videos=[foreign])
    gateway = make_cached(upstream)
    await gateway.videos_by_id([foreign.video_id], CTX, with_localizations=True)
    again = await gateway.videos_by_id([foreign.video_id], CTX, with_localizations=True)
    assert upstream.calls.count("videos.list") == 1
    assert again.found[0].localizations == {}
    await gateway.videos_by_id([foreign.video_id], request_context(QuotaPriority.interactive, max_age_s=0))
    await gateway.videos_by_id([foreign.video_id], CTX, with_localizations=True)
    assert upstream.calls.count("videos.list") == 2, "a plain refetch keeps the cached localization state"


class FlakyHydration(PersistingGateway):
    fail_next = False

    async def videos_by_id(self, ids, ctx, with_localizations=False):
        if self.fail_next:
            self.fail_next = False
            raise Transient("backend error", status=503)
        return await super().videos_by_id(ids, ctx, with_localizations)


async def test_the_uploads_etag_is_saved_only_after_new_videos_are_hydrated(clock, store) -> None:
    items, videos = uploads(60, every_h=45)
    gateway = FlakyHydration(
        clock, channels=[channel_record(CHANNEL_A)], videos=videos, uploads={PLAYLIST: items}
    )
    gateway.store = store
    scheduler = SnapshotScheduler(store, CadencePlanner(YouTubeSnapshotSettings(), horizon_days=180), clock)
    ingest = IngestService(
        gateway, store, scheduler, YouTubeIngestSettings(), YouTubeFreshnessSettings(), clock
    )
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX)
    new_items, new_videos = uploads(3, every_h=1, start_index=500)
    gateway.uploads[PLAYLIST] = new_items + gateway.uploads[PLAYLIST]
    gateway.videos.update({video.video_id: video for video in new_videos})
    clock.advance(timedelta(hours=7))
    gateway.fail_next = True
    failed = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX, mode="incremental")
    assert failed.status == "unavailable"
    clock.advance(timedelta(hours=7))
    retried = await ingest.ingest_channel(CHANNEL_A, ChannelRole.competitor, CTX, mode="incremental")
    assert set(retried.new_video_ids) == {video.video_id for video in new_videos}


class RejectingStats(FakeGateway):
    bad: set[str] = set()
    error: Exception | None = None
    stats_calls: list[list[str]]

    async def video_stats(self, ids, ctx):
        self.stats_calls.append(list(ids))
        if self.error is not None:
            raise self.error
        if self.bad & set(ids):
            raise InvalidRequest("bad id in batch", status=400, reason="invalidVideoId")
        return await super().video_stats(ids, ctx)


async def schedule_rows(store) -> dict[str, tuple[int, datetime]]:
    async with store.uow() as uow:
        rows = (await uow.session.execute(select(YtSnapshotSchedule))).scalars().all()
        return {row.video_id: (row.failed_attempts, row.next_due_at) for row in rows}


async def test_a_rejected_chunk_is_bisected_to_the_bad_id_and_only_it_backs_off(clock, store) -> None:
    gateway, ingest, scheduler = build(clock, store, count=8, every_h=100)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    rejecting = RejectingStats(
        clock, channels=list(gateway.channels.values()), videos=list(gateway.videos.values())
    )
    rejecting.stats_calls, rejecting.bad = [], {video_id(3)}
    clock.advance(timedelta(days=2))
    report = await SnapshotCollector(rejecting, scheduler, YouTubeSnapshotSettings(), clock).collect()
    assert report.captured == 7 and report.errors == ["InvalidRequest"]
    rows = await schedule_rows(store)
    assert rows[video_id(3)] == (1, clock.now() + timedelta(minutes=10))
    assert all(rows[video_id(i)][0] == 0 for i in range(8) if i != 3)


async def test_a_chunk_that_keeps_failing_backs_off_exponentially(clock, store) -> None:
    gateway, ingest, scheduler = build(clock, store, count=3, every_h=100)
    await persist(store, gateway)
    await ingest.ingest_channel(CHANNEL_A, ChannelRole.own, CTX)
    failing = RejectingStats(
        clock, channels=list(gateway.channels.values()), videos=list(gateway.videos.values())
    )
    failing.stats_calls, failing.error = [], Transient("backend error", status=503)
    collector = SnapshotCollector(failing, scheduler, YouTubeSnapshotSettings(), clock)
    clock.advance(timedelta(days=2))
    delays = []
    for _ in range(3):
        await collector.collect()
        due = await scheduler.due_batch(10)
        assert due == [], "a failed chunk is not due again on the next tick"
        _, next_due_at = (await schedule_rows(store))[video_id(0)]
        delays.append(next_due_at - clock.now())
        clock.advance(next_due_at - clock.now())
    assert delays == [timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=40)]
    assert len(failing.stats_calls) == 3, "transient errors are not bisected"
