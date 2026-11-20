from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.adapters.youtube.errors import CircuitOpen, Forbidden, InvalidPageToken, NotFound, Transient
from app.core.clock import Clock
from app.core.config import YouTubeFreshnessSettings, YouTubeIngestSettings
from app.core.logging import get_logger
from app.domain.common import ChannelRole, VideoFormat
from app.domain.youtube import ChannelRecord, QuotaBucket, VideoRecord
from app.ports.quota import QuotaExhausted
from app.ports.youtube import NotModified, PlaylistItem, RequestContext, YouTubeGateway
from app.services.youtube import messages
from app.services.youtube.context import as_youtube_context, with_max_age
from app.services.youtube.snapshots import SnapshotScheduler
from app.services.youtube.store import YouTubeStore

logger = get_logger(__name__)

IngestMode = Literal["full", "incremental", "baseline"]
COUNTED_FORMATS = (VideoFormat.short_form, VideoFormat.long_form)


@dataclass
class LookbackState:
    collected: int = 0
    per_format: Counter = field(default_factory=Counter)
    consecutive_old: int = 0
    oldest: datetime | None = None


class LookbackPlanner:
    """enough history for baselines and the largest window, never the full archive (99 c14)."""

    def __init__(self, settings: YouTubeIngestSettings, now: datetime, cap: int) -> None:
        self._settings = settings
        self.cap = cap
        self.min_cutoff = now - timedelta(days=settings.window_max_days + settings.baseline_extra_days)
        self.max_cutoff = now - timedelta(days=settings.max_horizon_days)
        self.state = LookbackState()

    def accept(self, item: PlaylistItem) -> bool:
        state = self.state
        state.oldest = item.published_at if state.oldest is None else min(state.oldest, item.published_at)
        if item.published_at < self.max_cutoff:
            state.consecutive_old += 1
            return False
        state.consecutive_old = state.consecutive_old + 1 if item.published_at < self.min_cutoff else 0
        return True

    def count(self, videos: Sequence[VideoRecord]) -> None:
        self.state.collected += len(videos)
        self.state.per_format.update(video.format for video in videos)

    def formats_satisfied(self) -> bool:
        return all(self.state.per_format[fmt] >= self._settings.min_per_format for fmt in COUNTED_FORMATS)

    def done(self) -> bool:
        state = self.state
        if state.collected >= self.cap:
            return True
        past_window = state.oldest is not None and state.oldest < self.min_cutoff
        if past_window and self.formats_satisfied():
            return True
        return state.consecutive_old >= self._settings.older_run_to_stop and (
            self.formats_satisfied() or (state.oldest is not None and state.oldest < self.max_cutoff)
        )


@dataclass
class ChannelIngestResult:
    channel_id: str
    status: Literal["ok", "not_found", "forbidden", "quota_limited", "unavailable"] = "ok"
    channel: ChannelRecord | None = None
    new_video_ids: list[str] = field(default_factory=list)
    videos: list[VideoRecord] = field(default_factory=list)
    pages: int = 0
    stats_refreshed: int = 0
    flags: list[str] = field(default_factory=list)
    message: str | None = None
    quota_bucket: QuotaBucket | None = None
    quota_resets_at: datetime | None = None


class IngestService:
    def __init__(
        self,
        gateway: YouTubeGateway,
        store: YouTubeStore,
        scheduler: SnapshotScheduler,
        settings: YouTubeIngestSettings,
        freshness: YouTubeFreshnessSettings,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._scheduler = scheduler
        self._settings = settings
        self._clock = clock
        self._own_max_age_s = freshness.own_video_metadata_h * 3600
        self._freshness = freshness

    async def ingest_channel(
        self,
        channel_id: str,
        role: ChannelRole,
        ctx: RequestContext,
        *,
        mode: IngestMode = "full",
        languages: Sequence[str] = (),
    ) -> ChannelIngestResult:
        result = ChannelIngestResult(channel_id=channel_id)
        video_ctx = (
            with_max_age(ctx, self._own_max_age_s) if role is ChannelRole.own else as_youtube_context(ctx)
        )
        try:
            await self._ingest(
                result, role, ctx, video_ctx, mode, languages or self._settings.localization_languages
            )
        except QuotaExhausted as error:
            result.status = "quota_limited"
            result.quota_bucket, result.quota_resets_at = error.bucket, error.resets_at
            logger.info("ingest_quota_limited", channel_id=channel_id, bucket=error.bucket.value)
        except Forbidden:
            result.status, result.message = "forbidden", messages.FORBIDDEN
        except (Transient, CircuitOpen):
            # whatever was fetched before the failure is already in the shared cache
            result.status, result.message = "unavailable", messages.PARTIAL
        return result

    async def _ingest(
        self,
        result: ChannelIngestResult,
        role: ChannelRole,
        ctx: RequestContext,
        video_ctx: RequestContext,
        mode: IngestMode,
        languages: Sequence[str],
    ) -> None:
        found = await self._gateway.channels_by_id([result.channel_id], ctx)
        if not found:
            result.status, result.message = "not_found", messages.channel_gone(result.channel_id)
            return
        channel = result.channel = found[0]
        if not channel.uploads_playlist_id:
            result.flags.append("very_new_channel")
            result.message = messages.NO_UPLOADS
            await self._finish(result, channel, overflow=False)
            return
        row = (await self._store.channels([channel.channel_id])).get(channel.channel_id)
        synced_at = row.uploads_synced_at if row else None
        now = self._clock.now()
        if mode == "incremental" and synced_at is not None:
            ttl = timedelta(hours=self._uploads_ttl(role))
            overflow = False
            if now - synced_at >= ttl:
                overflow = await self._incremental(result, channel, video_ctx, languages)
            await self._refresh_window_stats(result, channel, ctx)
            await self._finish(result, channel, overflow=overflow)
            return
        light = mode == "baseline" or role in (ChannelRole.niche, ChannelRole.creator)
        cap = self._settings.max_videos_niche if light else self._settings.max_videos_channel
        await self._full(result, channel, video_ctx, LookbackPlanner(self._settings, now, cap), languages)
        await self._finish(result, channel, overflow=False)

    def _uploads_ttl(self, role: ChannelRole) -> int:
        if role in (ChannelRole.own, ChannelRole.competitor):
            return self._freshness.uploads_own_competitor_h
        return self._freshness.uploads_niche_h

    async def _pages(self, playlist_id: str, ctx: RequestContext, max_pages: int, etag_key: str | None):
        token: str | None = None
        restarted = False
        page_number = 0
        while page_number < max_pages:
            etag = await self._store.etag(etag_key) if etag_key and page_number == 0 else None
            try:
                page = await self._gateway.uploads_page(playlist_id, token, ctx, etag)
            except InvalidPageToken:
                if restarted:
                    return
                restarted, token, page_number = True, None, 0
                continue
            except NotFound:
                return
            if isinstance(page, NotModified):
                yield page
                return
            if etag_key and page_number == 0 and page.etag:
                await self._store.save_etag(etag_key, page.etag, self._clock.now())
            page_number += 1
            yield page
            token = page.next_page_token
            if not token:
                return

    async def _full(
        self,
        result: ChannelIngestResult,
        channel: ChannelRecord,
        ctx: RequestContext,
        planner: LookbackPlanner,
        languages: Sequence[str],
    ) -> None:
        max_pages = -(-planner.cap // self._settings.page_size)
        seen: set[str] = set()
        async for page in self._pages(channel.uploads_playlist_id, ctx, max_pages, None):
            result.pages += 1
            ids = []
            for item in page.items:
                if item.video_id in seen:
                    continue
                seen.add(item.video_id)
                if planner.accept(item):
                    ids.append(item.video_id)
            ids = ids[: max(0, planner.cap - planner.state.collected)]
            videos = await self._hydrate(ids, ctx, languages)
            planner.count(videos)
            result.videos += videos
            if planner.done():
                break
        result.new_video_ids = [video.video_id for video in result.videos]

    async def _incremental(
        self,
        result: ChannelIngestResult,
        channel: ChannelRecord,
        ctx: RequestContext,
        languages: Sequence[str],
    ) -> bool:
        etag_key = f"playlistItems:{channel.uploads_playlist_id}:first"
        run_known = 0
        new_ids: list[str] = []
        pages = self._pages(channel.uploads_playlist_id, ctx, self._settings.incremental_max_pages, etag_key)
        exhausted_pages = True
        async for page in pages:
            if isinstance(page, NotModified):
                return False
            result.pages += 1
            known = await self._store.videos([item.video_id for item in page.items])
            for item in page.items:
                if item.video_id in known:
                    run_known += 1
                    if run_known >= self._settings.known_run_to_stop:
                        exhausted_pages = False
                        break
                    continue
                run_known = 0
                if item.video_id not in new_ids:
                    new_ids.append(item.video_id)
            if not exhausted_pages:
                break
        if new_ids:
            videos = await self._hydrate(new_ids, ctx, languages)
            result.videos += videos
            result.new_video_ids = [video.video_id for video in videos]
        return exhausted_pages and result.pages >= self._settings.incremental_max_pages

    async def _hydrate(
        self, ids: Sequence[str], ctx: RequestContext, languages: Sequence[str]
    ) -> list[VideoRecord]:
        if not ids:
            return []
        batch = await self._gateway.videos_by_id(ids, ctx)
        foreign = [
            video.video_id
            for video in batch.found
            if video.default_language
            and video.default_language.split("-")[0] not in languages
            and not video.localizations
        ]
        if foreign:
            localized = await self._gateway.videos_by_id(foreign, ctx, with_localizations=True)
            by_id = {video.video_id: video for video in localized.found}
            found = [by_id.get(video.video_id, video) for video in batch.found]
        else:
            found = batch.found
        return [video for video in found if video.privacy_status == "public"]

    async def _refresh_window_stats(
        self, result: ChannelIngestResult, channel: ChannelRecord, ctx: RequestContext
    ) -> None:
        since = self._clock.now() - timedelta(days=self._settings.window_max_days)
        rows = await self._store.channel_videos(channel.channel_id, published_after=since)
        fresh = set(result.new_video_ids)
        ids = [row.video_id for row in rows if row.video_id not in fresh]
        if ids:
            batch = await self._gateway.video_stats(ids, ctx)
            result.stats_refreshed = len(batch.snapshots)

    async def _finish(self, result: ChannelIngestResult, channel: ChannelRecord, *, overflow: bool) -> None:
        now = self._clock.now()
        horizon = now - timedelta(days=self._settings.max_horizon_days)
        rows = await self._store.channel_videos(channel.channel_id, published_after=horizon)
        formats = Counter(row.format for row in rows)
        flags = set(result.flags)
        if len(rows) < self._settings.very_new_channel_videos:
            flags.add("very_new_channel")
        counted = formats[VideoFormat.short_form.value] + formats[VideoFormat.long_form.value]
        if counted >= self._settings.very_new_channel_videos:
            if formats[VideoFormat.long_form.value] == 0:
                flags.add("shorts_only")
            elif formats[VideoFormat.short_form.value] == 0:
                flags.add("long_form_only")
        if overflow:
            flags.add("high_cadence")
        result.flags = sorted(flags)
        if "shorts_only" in flags and result.message is None:
            result.message = messages.shorts_only()
        if "very_new_channel" in flags and result.message is None and rows:
            result.message = messages.VERY_NEW
        await self._flag_duplicate_titles(rows)
        window = now - timedelta(days=90)
        await self._store.update_channel(
            channel.channel_id,
            uploads_synced_at=now,
            uploads_synced_through=max((row.published_at for row in rows), default=None),
            uploads_90d=sum(1 for row in rows if row.published_at >= window),
            last_upload_at=max((row.published_at for row in rows), default=None),
            recent_upload_ids=[row.video_id for row in rows[:50]],
            cadence_flags=result.flags,
            backfill_horizon=horizon,
            backfill_complete=True,
        )
        await self._scheduler.track(
            [
                (video.video_id, video.effective_published_at, video.metadata_fetched_at)
                for video in result.videos
            ]
        )

    async def _flag_duplicate_titles(self, rows: Sequence) -> None:
        window = timedelta(days=self._settings.duplicate_title_days)
        by_title: dict[str, list] = {}
        for row in sorted(rows, key=lambda r: r.published_at):
            title = " ".join((row.title or "").casefold().split())
            if title:
                by_title.setdefault(title, []).append(row)
        duplicates = []
        for group in by_title.values():
            for earlier, later in zip(group, group[1:], strict=False):
                if later.published_at - earlier.published_at <= window:
                    duplicates.append(later.video_id)
        await self._store.add_quality_flag(duplicates, "duplicate_title_in_channel")

    async def ensure_baseline(
        self, channel_ids: Sequence[str], ctx: RequestContext
    ) -> list[ChannelIngestResult]:
        """lightweight baseline (one page of uploads) for creators and demand-check channels (99 g4)."""
        results = []
        for channel_id in channel_ids:
            results.append(await self.ingest_channel(channel_id, ChannelRole.creator, ctx, mode="baseline"))
            if results[-1].status == "quota_limited":
                break
        return results

    async def recent_uploads(
        self, channel_id: str, ctx: RequestContext, limit: int = 50
    ) -> list[VideoRecord]:
        found = await self._gateway.channels_by_id([channel_id], ctx)
        if not found or not found[0].uploads_playlist_id:
            return []
        try:
            page = await self._gateway.uploads_page(found[0].uploads_playlist_id, None, ctx)
        except NotFound:
            return []
        if isinstance(page, NotModified):
            return []
        ids = [item.video_id for item in page.items][:limit]
        return await self._hydrate(ids, ctx, self._settings.localization_languages)
