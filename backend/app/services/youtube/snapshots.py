from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.adapters.youtube.errors import YouTubeApiError
from app.core.clock import Clock
from app.core.config import YouTubeSnapshotSettings
from app.core.logging import get_logger
from app.core.registry import Registry
from app.domain.youtube import QuotaPriority, VideoSnapshot
from app.models.youtube_cache import YtSnapshotSchedule, YtVideo
from app.ports.quota import QuotaExhausted
from app.ports.youtube import YouTubeGateway
from app.retention.sweeper import REFERENCED_VIDEOS_SQL
from app.services.youtube.context import request_context
from app.services.youtube.store import YouTubeStore

logger = get_logger(__name__)

HOUR = timedelta(hours=1)


def counting_regime_for(settings: YouTubeSnapshotSettings) -> Callable[[datetime], str]:
    regimes = sorted(settings.counting_regimes, key=lambda regime: regime.since)

    def regime(captured_at: datetime) -> str:
        label = "standard"
        for candidate in regimes:
            if captured_at.date() >= candidate.since:
                label = candidate.label
        return label

    return regime


@dataclass(frozen=True)
class CadenceTier:
    name: str
    max_age_h: float
    cadence_h: float


snapshot_cadence_registry: Registry[CadenceTier] = Registry("youtube_snapshot_cadence")
for _tier in (
    CadenceTier("hourly", 48, 1),
    CadenceTier("six_hourly", 8 * 24, 6),
    CadenceTier("daily", 35 * 24, 24),
):
    snapshot_cadence_registry.add(_tier.name, _tier)


@dataclass(frozen=True)
class SchedulePlan:
    next_due_at: datetime
    tier: str
    milestone_target_h: int | None
    priority: int


@dataclass(frozen=True)
class DueVideo:
    video_id: str
    effective_published_at: datetime
    milestone_target_h: int | None


class CadencePlanner:
    """adaptive cadence by age with milestone shots at day 1, 3, 7 and 30."""

    def __init__(
        self,
        settings: YouTubeSnapshotSettings,
        horizon_days: int,
        tiers: Registry[CadenceTier] = snapshot_cadence_registry,
    ) -> None:
        self._settings = settings
        weekly = CadenceTier("weekly", horizon_days * 24, 7 * 24)
        self._tiers = sorted([*tiers.all().values(), weekly], key=lambda tier: tier.max_age_h)

    def tier_for(self, age_h: float) -> tuple[int, CadenceTier] | None:
        for index, tier in enumerate(self._tiers):
            if age_h < tier.max_age_h:
                return index, tier
        return None

    def plan(
        self, effective_published_at: datetime, now: datetime, last_captured_at: datetime | None
    ) -> SchedulePlan | None:
        age_h = (now - effective_published_at) / HOUR
        found = self.tier_for(max(0.0, age_h))
        if found is None:
            return None
        index, tier = found
        cadence_due = now if last_captured_at is None else last_captured_at + timedelta(hours=tier.cadence_h)
        best = SchedulePlan(
            next_due_at=cadence_due, tier=tier.name, milestone_target_h=None, priority=index + 1
        )
        for target_h, lead_h in sorted(self._settings.milestones_h.items()):
            target = effective_published_at + timedelta(hours=target_h)
            window_start, window_end = target - timedelta(hours=lead_h), target + timedelta(hours=lead_h)
            captured = last_captured_at is not None and last_captured_at >= window_start
            if now > window_end or captured:
                continue
            due = max(window_start, now)
            if due <= best.next_due_at:
                best = SchedulePlan(next_due_at=due, tier=tier.name, milestone_target_h=target_h, priority=0)
            break
        return best


class SnapshotScheduler:
    def __init__(self, store: YouTubeStore, planner: CadencePlanner, clock: Clock) -> None:
        self._store = store
        self._planner = planner
        self._clock = clock

    async def track(self, videos: Sequence[tuple[str, datetime, datetime | None]]) -> int:
        """videos are (video_id, effective_published_at, last_captured_at); existing rows are kept."""
        now = self._clock.now()
        rows = []
        for video_id, effective, last_captured in videos:
            plan = self._planner.plan(effective, now, last_captured)
            if plan is None:
                continue
            rows.append(
                {
                    "video_id": video_id,
                    "next_due_at": plan.next_due_at,
                    "tier": plan.tier,
                    "milestone_target_h": plan.milestone_target_h,
                    "priority": plan.priority,
                    "tracking_started_at": now,
                    "first_seen_age_h": max(0.0, (now - effective) / HOUR),
                    "last_captured_at": last_captured,
                }
            )
        if not rows:
            return 0
        async with self._store.uow() as uow:
            result = await uow.session.execute(
                insert(YtSnapshotSchedule).values(rows).on_conflict_do_nothing(index_elements=["video_id"])
            )
            await uow.commit()
        return result.rowcount

    async def due_batch(self, limit: int) -> list[DueVideo]:
        now = self._clock.now()
        statement = (
            select(
                YtSnapshotSchedule.video_id,
                YtVideo.effective_published_at,
                YtSnapshotSchedule.milestone_target_h,
            )
            .join(YtVideo, YtVideo.video_id == YtSnapshotSchedule.video_id)
            .where(YtSnapshotSchedule.next_due_at <= now, YtVideo.availability != "removed")
            .order_by(
                YtSnapshotSchedule.milestone_target_h.is_(None),
                YtSnapshotSchedule.priority,
                YtSnapshotSchedule.next_due_at,
            )
            .limit(limit)
        )
        async with self._store.uow() as uow:
            rows = (await uow.session.execute(statement)).all()
        return [DueVideo(video_id, effective, milestone) for video_id, effective, milestone in rows]

    async def reschedule(self, captured: Sequence[DueVideo], captured_at: datetime | None) -> None:
        now = self._clock.now()
        untracked = []
        async with self._store.uow() as uow:
            for video in captured:
                plan = self._planner.plan(video.effective_published_at, now, captured_at)
                if plan is None:
                    untracked.append(video.video_id)
                    continue
                values = {
                    "next_due_at": plan.next_due_at,
                    "tier": plan.tier,
                    "milestone_target_h": plan.milestone_target_h,
                    "priority": plan.priority,
                }
                if captured_at is not None:
                    values["last_captured_at"] = captured_at
                await uow.session.execute(
                    update(YtSnapshotSchedule)
                    .where(YtSnapshotSchedule.video_id == video.video_id)
                    .values(**values)
                )
            if untracked:
                await uow.session.execute(
                    delete(YtSnapshotSchedule).where(YtSnapshotSchedule.video_id.in_(untracked))
                )
            await uow.commit()

    async def prune(self) -> int:
        # tracked while an active client watches the channel, or a recommendation or search result points at it
        referenced = (
            f"{REFERENCED_VIDEOS_SQL} UNION SELECT resource_id FROM yt_search_result WHERE kind = 'video'"
        )
        async with self._store.uow() as uow:
            result = await uow.session.execute(
                text(f"DELETE FROM yt_snapshot_schedule WHERE video_id NOT IN ({referenced})")
            )
            removed = await uow.session.execute(
                text(
                    "DELETE FROM yt_snapshot_schedule USING yt_videos WHERE yt_videos.video_id = "
                    "yt_snapshot_schedule.video_id AND yt_videos.availability = 'removed'"
                )
            )
            await uow.commit()
        return result.rowcount + removed.rowcount


@dataclass
class CollectReport:
    due: int = 0
    captured: int = 0
    fallback: int = 0
    missing: int = 0
    quota_limited: bool = False
    errors: list[str] = field(default_factory=list)


class SnapshotCollector:
    """runs every 10 minutes: due videos in 50-id batchGetStats calls, milestone-near fallback to videos.list."""

    def __init__(
        self,
        gateway: YouTubeGateway,
        scheduler: SnapshotScheduler,
        settings: YouTubeSnapshotSettings,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self._scheduler = scheduler
        self._settings = settings
        self._clock = clock

    def milestone_near(self, video: DueVideo, now: datetime) -> bool:
        age_h = (now - video.effective_published_at) / HOUR
        window = self._settings.fallback_window_h
        return any(abs(age_h - target) <= window for target in self._settings.fallback_milestones_h)

    async def collect(self) -> CollectReport:
        report = CollectReport()
        size = self._settings.batch_size
        due = await self._scheduler.due_batch(size * self._settings.max_batches_per_tick)
        report.due = len(due)
        ctx = request_context(QuotaPriority.snapshot)
        for start in range(0, len(due), size):
            chunk = due[start : start + size]
            try:
                batch = await self._gateway.video_stats([video.video_id for video in chunk], ctx)
            except (QuotaExhausted, YouTubeApiError) as error:
                report.errors.append(type(error).__name__)
                report.quota_limited |= isinstance(error, QuotaExhausted)
                await self._fallback(chunk, report)
                if isinstance(error, QuotaExhausted):
                    await self._fallback_rest(due[start + size :], report)
                    break
                continue
            report.captured += len(batch.snapshots)
            report.missing += len(batch.missing_ids)
            await self._scheduler.reschedule(chunk, self._clock.now())
        return report

    async def _fallback(self, chunk: Sequence[DueVideo], report: CollectReport) -> None:
        now = self._clock.now()
        near = [video for video in chunk if self.milestone_near(video, now)]
        far = [video for video in chunk if video not in near]
        if near:
            fallback_ctx = request_context(QuotaPriority.snapshot, max_age_s=0)
            try:
                batch = await self._gateway.videos_by_id([video.video_id for video in near], fallback_ctx)
                report.fallback += len(batch.found)
                await self._scheduler.reschedule(near, self._clock.now())
            except (QuotaExhausted, YouTubeApiError) as error:
                report.errors.append(f"fallback:{type(error).__name__}")
                far = list(chunk)
        await self._scheduler.reschedule(far, None)

    async def _fallback_rest(self, rest: Sequence[DueVideo], report: CollectReport) -> None:
        size = self._settings.batch_size
        for start in range(0, len(rest), size):
            await self._fallback(rest[start : start + size], report)


def snapshots_to_drop(
    snapshots: Sequence[VideoSnapshot], current_age_h: float, settings: YouTubeSnapshotSettings
) -> list[VideoSnapshot]:
    """thins pre-day-8 snapshots to milestone brackets plus one per day, and older ones to one per week."""
    compact_h = settings.compact_after_days * 24
    if current_age_h <= compact_h:
        return []
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.age_hours)
    keep: set[datetime] = set()
    for target in settings.milestones_h:
        before = [s for s in ordered if s.age_hours <= target]
        after = [s for s in ordered if s.age_hours >= target]
        if before:
            keep.add(before[-1].slot_at)
        if after:
            keep.add(after[0].slot_at)
    weekly = current_age_h > settings.weekly_after_days * 24
    seen_buckets: set[tuple[str, int]] = set()
    for snapshot in ordered:
        if snapshot.age_hours < compact_h or not weekly:
            bucket = ("day", int(snapshot.age_hours // 24))
        else:
            bucket = ("week", int(snapshot.age_hours // (24 * 7)))
        if bucket not in seen_buckets:
            seen_buckets.add(bucket)
            keep.add(snapshot.slot_at)
    if not weekly:
        keep.update(s.slot_at for s in ordered if s.age_hours >= compact_h)
    return [snapshot for snapshot in ordered if snapshot.slot_at not in keep]


class SnapshotCompactor:
    def __init__(self, store: YouTubeStore, settings: YouTubeSnapshotSettings, clock: Clock) -> None:
        self._store = store
        self._settings = settings
        self._clock = clock

    async def compact(self, video_ids: Sequence[str]) -> int:
        now = self._clock.now()
        videos = await self._store.videos(video_ids)
        series = await self._store.snapshots(list(videos))
        doomed: list[tuple[str, datetime]] = []
        for video_id, snapshots in series.items():
            age_h = (now - videos[video_id].effective_published_at) / HOUR
            doomed += [(s.video_id, s.slot_at) for s in snapshots_to_drop(snapshots, age_h, self._settings)]
        return await self._store.delete_snapshots(doomed)

    async def compact_tracked(self, limit: int = 5000) -> int:
        # only series past day 8 have anything to thin
        statement = (
            select(YtSnapshotSchedule.video_id)
            .where(YtSnapshotSchedule.tier.in_(("daily", "weekly")))
            .order_by(YtSnapshotSchedule.video_id)
            .limit(limit)
        )
        async with self._store.uow() as uow:
            video_ids = list((await uow.session.execute(statement)).scalars())
        return await self.compact(video_ids)


class SnapshotReader:
    """raw snapshots for 06's views-at-age resolver; this layer does not interpolate."""

    def __init__(self, store: YouTubeStore) -> None:
        self._store = store

    async def snapshots(self, video_id: str, until: datetime | None = None) -> list[VideoSnapshot]:
        return (await self._store.snapshots([video_id], until)).get(video_id, [])

    async def snapshots_many(
        self, video_ids: Sequence[str], until: datetime | None = None
    ) -> dict[str, list[VideoSnapshot]]:
        return await self._store.snapshots(video_ids, until)
