import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from app.adapters.youtube.data_api_v3 import DataApiV3Gateway, unique
from app.adapters.youtube.fallback import FallbackGateway
from app.core.clock import Clock
from app.core.redis import LockManager
from app.domain.common import ChannelId, VideoId
from app.domain.youtube import ChannelRecord, SearchHit, SearchPage, SearchQuery, VideoRecord, VideoSnapshot
from app.models.youtube_cache import YtVideo
from app.ports.youtube import CommentPage, NotModified, RequestContext, StatsBatch, UploadsPage, VideoBatch
from app.repositories.shared import SearchCacheRepository, SearchResultItem
from app.services.youtube.cache_policy import DataKind, FreshnessPolicy
from app.services.youtube.context import as_youtube_context
from app.services.youtube.ledger import CacheHitCounter
from app.services.youtube.search import search_key, search_params_for_key
from app.services.youtube.single_flight import BatchCoalescer, InflightClaims
from app.services.youtube.store import YouTubeStore, row_to_channel, row_to_video


def quota_identity(ctx: RequestContext) -> tuple:
    return (ctx.client_id, ctx.run_id)


class CachedGateway:
    """outermost layer: a shared postgres cache written through once, so cache hits never touch quota."""

    def __init__(
        self,
        inner: DataApiV3Gateway | FallbackGateway,
        store: YouTubeStore,
        policy: FreshnessPolicy,
        clock: Clock,
        *,
        locks: LockManager | None = None,
        inflight: InflightClaims | None = None,
        hits: CacheHitCounter | None = None,
        coalesce_window_s: float = 0.15,
        removed_after: timedelta = timedelta(hours=6),
        counting_regime: Callable[[datetime], str] = lambda _: "standard",
    ) -> None:
        self._inner = inner
        self._store = store
        self._policy = policy
        self._clock = clock
        self._locks = locks
        self._inflight = inflight
        self._hits = hits
        self._window_s = coalesce_window_s
        self._removed_after = removed_after
        self._counting_regime = counting_regime
        self._coalescers: dict[tuple, BatchCoalescer] = {}

    async def _count_hits(self, ctx: RequestContext, kind: DataKind, count: int) -> None:
        if self._hits is not None and count:
            await self._hits.hit(ctx.client_id, kind.value, count)

    async def channels_by_id(self, ids: Sequence[ChannelId], ctx: RequestContext) -> list[ChannelRecord]:
        wanted = unique(ids)
        now = self._clock.now()
        max_age = as_youtube_context(ctx).max_age_s
        rows = await self._store.channels(wanted)
        fresh: dict[str, ChannelRecord] = {}
        known_missing: set[str] = set()
        for channel_id, row in rows.items():
            if not (
                self._policy.is_fresh(row.metadata_fetched_at, DataKind.channel_metadata, now, max_age)
                and self._policy.is_fresh(row.stats_fetched_at, DataKind.channel_stats, now, max_age)
            ):
                continue
            if row.availability == "available":
                fresh[channel_id] = row_to_channel(row)
            else:
                known_missing.add(channel_id)
        await self._count_hits(ctx, DataKind.channel_metadata, len(fresh) + len(known_missing))
        stale = [
            channel_id for channel_id in wanted if channel_id not in fresh and channel_id not in known_missing
        ]
        if stale:
            fetched = await self._inner.channels_by_id(stale, ctx)
            await self._store.save_channels(fetched)
            found = {record.channel_id for record in fetched}
            await self._store.mark_channels_unavailable(
                [c for c in stale if c not in found and c in rows], now
            )
            # re-read so ingest-computed fields (uploads_90d, recent uploads, flags) come back too
            stored = await self._store.channels(list(found))
            fresh.update({channel_id: row_to_channel(row) for channel_id, row in stored.items()})
        return [fresh[channel_id] for channel_id in wanted if channel_id in fresh]

    async def _alias(self, kind: str, value: str, ctx: RequestContext) -> ChannelRecord | None:
        now = self._clock.now()
        cached = await self._store.resolution(kind, value, now)
        if cached is not None:
            await self._count_hits(ctx, DataKind.alias, 1)
            if cached.channel_id is None:
                return None
            found = await self.channels_by_id([cached.channel_id], ctx)
            return found[0] if found else None
        lookup = self._inner.channel_by_handle if kind == "handle" else self._inner.channel_by_username
        record = await lookup(value, ctx)
        if record is not None:
            await self._store.save_channels([record])
        ttl_kind = DataKind.alias if record else DataKind.alias_not_found
        await self._store.save_resolution(
            kind,
            value,
            record.channel_id if record else None,
            f"for{kind.title()}",
            now,
            self._policy.ttl(ttl_kind),
        )
        return record

    async def _single_flight_alias(self, kind: str, value: str, ctx: RequestContext) -> ChannelRecord | None:
        if self._locks is None:
            return await self._alias(kind, value, ctx)
        async with self._locks.acquire(f"sf:yt_alias:{kind}:{value}", ttl_s=60, wait_s=60):
            return await self._alias(kind, value, ctx)

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None:
        value = unicodedata.normalize("NFC", handle.strip()).lstrip("@").casefold()
        return await self._single_flight_alias("handle", value, ctx)

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None:
        return await self._single_flight_alias("username", username.strip(), ctx)

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified:
        return await self._inner.uploads_page(playlist_id, page_token, ctx, etag)

    def _coalescer(self, ctx: RequestContext, with_localizations: bool) -> BatchCoalescer[VideoRecord]:
        # batches never mix priority classes, so a snapshot batch cannot block interactive work
        key = (ctx.priority, with_localizations)
        if key not in self._coalescers:

            async def fetch(ids: list[str], caller: RequestContext) -> dict[str, VideoRecord]:
                batch = await self._fetch_videos(ids, caller, with_localizations)
                return {video.video_id: video for video in batch}

            self._coalescers[key] = BatchCoalescer(
                fetch, max_batch=50, window_s=self._window_s, group_key=quota_identity
            )
        return self._coalescers[key]

    async def _fetch_videos(
        self, ids: list[str], ctx: RequestContext, with_localizations: bool
    ) -> list[VideoRecord]:
        started = self._clock.now()
        token, mine, others = (None, ids, [])
        if self._inflight is not None:
            token, mine, others = await self._inflight.claim("video", ids)
        try:
            fetched = await self._fetch_upstream(mine, ctx, with_localizations)
        finally:
            if self._inflight is not None and token is not None:
                await self._inflight.release("video", token, mine)
        if others and self._inflight is not None:
            await self._inflight.wait("video", others)
            rows = await self._store.videos(others)
            clock_column = "localizations_fetched_at" if with_localizations else "metadata_fetched_at"
            landed = {
                video_id: row
                for video_id, row in rows.items()
                if (getattr(row, clock_column) or datetime.min.replace(tzinfo=UTC)) >= started
            }
            fetched += [row_to_video(row) for row in landed.values() if row.availability == "available"]
            # ids whose other fetcher failed (say on its own quota cap) are fetched on this caller's quota
            unresolved = [video_id for video_id in others if video_id not in landed]
            fetched += await self._fetch_upstream(unresolved, ctx, with_localizations)
        return fetched

    async def _fetch_upstream(
        self, ids: list[str], ctx: RequestContext, with_localizations: bool
    ) -> list[VideoRecord]:
        if not ids:
            return []
        batch = await self._inner.videos_by_id(ids, ctx, with_localizations)
        await self._persist_videos(batch.found, ctx, localized=with_localizations)
        await self._store.record_misses(batch.missing_ids, self._clock.now(), self._removed_after)
        return batch.found

    async def _persist_videos(
        self, videos: Sequence[VideoRecord], ctx: RequestContext, *, localized: bool = False
    ) -> None:
        if not videos:
            return
        channel_ids = unique(video.channel_id for video in videos)
        known = await self._store.channels(channel_ids)
        missing_channels = [channel_id for channel_id in channel_ids if channel_id not in known]
        if missing_channels:
            await self.channels_by_id(missing_channels, ctx)
        stored = await self._store.channels(channel_ids)
        storable = [video for video in videos if video.channel_id in stored]
        snapshots = [hydration_snapshot(video, self._counting_regime) for video in storable]
        await self._store.save_videos(storable, snapshots, localized=localized)

    async def videos_by_id(
        self, ids: Sequence[VideoId], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch:
        wanted = unique(ids)
        now = self._clock.now()
        max_age = as_youtube_context(ctx).max_age_s
        rows = await self._store.videos(wanted)
        fresh: dict[str, VideoRecord] = {}
        removed: set[str] = set()
        for video_id, row in rows.items():
            if row.availability == "removed":
                removed.add(video_id)
            elif (
                row.availability == "available"
                and self._policy.is_fresh(row.metadata_fetched_at, DataKind.video_metadata, now, max_age)
                and (not with_localizations or self._localizations_fresh(row, now, max_age))
                and not broadcast_due(row, now)
            ):
                fresh[video_id] = row_to_video(row)
        await self._count_hits(ctx, DataKind.video_metadata, len(fresh))
        stale = [video_id for video_id in wanted if video_id not in fresh and video_id not in removed]
        if stale:
            fresh.update(await self._coalescer(ctx, with_localizations).get(stale, ctx))
        found = [fresh[video_id] for video_id in wanted if video_id in fresh]
        return VideoBatch(found=found, missing_ids=[video_id for video_id in wanted if video_id not in fresh])

    def _localizations_fresh(self, row: YtVideo, now: datetime, max_age: int | None) -> bool:
        # an empty result is cached too, so a video with no localizations is not re-requested every time
        fetched_at = row.localizations_fetched_at
        return fetched_at is not None and self._policy.is_fresh(
            fetched_at, DataKind.video_metadata, now, max_age
        )

    async def video_stats(self, ids: Sequence[VideoId], ctx: RequestContext) -> StatsBatch:
        batch = await self._inner.video_stats(ids, ctx)
        rows = await self._store.videos([snapshot.video_id for snapshot in batch.snapshots])
        aligned = [
            realign(snapshot, rows[snapshot.video_id].effective_published_at)
            if snapshot.video_id in rows
            else snapshot
            for snapshot in batch.snapshots
        ]
        await self._store.save_stats([snapshot for snapshot in aligned if snapshot.video_id in rows])
        # raw counts are kept; a drop (spam filtering) is flagged for 06's running-max handling
        decreased = [
            snapshot.video_id
            for snapshot in aligned
            if snapshot.video_id in rows and snapshot.view_count < (rows[snapshot.video_id].view_count or 0)
        ]
        await self._store.add_quality_flag(decreased, "count_decreased")
        await self._store.record_misses(batch.missing_ids, self._clock.now(), self._removed_after)
        return StatsBatch(snapshots=aligned, missing_ids=batch.missing_ids)

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage:
        key = search_key(query)
        cached = await self._cached_search(key, ctx)
        if cached is not None:
            return cached
        if self._locks is None:
            return await self._search(query, page_token, ctx, key)
        async with self._locks.acquire(f"sf:yt_search:{key}", ttl_s=60, wait_s=60):
            cached = await self._cached_search(key, ctx)
            return cached if cached is not None else await self._search(query, page_token, ctx, key)

    async def _cached_search(self, key: str, ctx: RequestContext) -> SearchPage | None:
        now = self._clock.now()
        async with self._store.uow() as uow:
            cached = await uow.repository(SearchCacheRepository).get_fresh(key, now=now)
            await uow.commit()
        if cached is None:
            return None
        await self._count_hits(ctx, DataKind.search, 1)
        items = [
            SearchHit(kind=item.kind, id=item.resource_id, channel_id=item.channel_id or "", rank=rank)
            for rank, item in enumerate(cached.items)
            if item.channel_id
        ]
        return SearchPage(
            query_key=key,
            items=items,
            next_page_token=cached.params.get("next_page_token"),
            fetched_at=cached.fetched_at,
            expires_at=cached.expires_at,
            from_cache=True,
        )

    async def _search(
        self, query: SearchQuery, page_token: str | None, ctx: RequestContext, key: str
    ) -> SearchPage:
        page = await self._inner.search(query, page_token, ctx)
        # a page that expires at once (an empty scrape) is returned but never cached
        if page.expires_at <= page.fetched_at:
            return page
        params = {**search_params_for_key(query), "next_page_token": page.next_page_token}
        async with self._store.uow() as uow:
            await uow.repository(SearchCacheRepository).put(
                query_key=key,
                endpoint="search.list",
                params=params,
                items=[SearchResultItem(hit.kind, hit.id, hit.channel_id) for hit in page.items],
                fetched_at=page.fetched_at,
                ttl=page.expires_at - page.fetched_at,
                quota_units=1,
                next_page_token=page.next_page_token,
            )
            await uow.commit()
        return page

    async def comment_threads(self, video_id: VideoId, max_results: int, ctx: RequestContext) -> CommentPage:
        return await self._inner.comment_threads(video_id, max_results, ctx)

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> ChannelId | None:
        return await self._inner.playlist_owner(playlist_id, ctx)

    async def own_channel_id(self, access_token: SecretStr, ctx: RequestContext) -> ChannelId | None:
        return await self._inner.own_channel_id(access_token, ctx)


def broadcast_due(row: YtVideo, now: datetime) -> bool:
    # upcoming and live videos are re-checked once their start time passes, and hourly while live
    if row.live_broadcast_content == "upcoming":
        return row.live_scheduled_start_at is None or row.live_scheduled_start_at <= now
    if row.live_broadcast_content == "live":
        return now - row.metadata_fetched_at >= timedelta(hours=1)
    return False


def hydration_snapshot(video: VideoRecord, counting_regime: Callable[[datetime], str]) -> VideoSnapshot:
    captured_at = video.metadata_fetched_at
    return VideoSnapshot(
        video_id=video.video_id,
        slot_at=captured_at.replace(minute=0, second=0, microsecond=0),
        captured_at=captured_at,
        age_hours=max(0.0, (captured_at - video.effective_published_at).total_seconds() / 3600),
        view_count=video.view_count,
        like_count=video.like_count,
        comment_count=video.comment_count,
        source="videos_list",
        counting_regime=counting_regime(captured_at),
    )


def realign(snapshot: VideoSnapshot, effective_published_at: datetime) -> VideoSnapshot:
    # batchGetStats reports publishTime; lives age from their actual start instead
    age = max(0.0, (snapshot.captured_at - effective_published_at).total_seconds() / 3600)
    return snapshot.model_copy(update={"age_hours": age})
