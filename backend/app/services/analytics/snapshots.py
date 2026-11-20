from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.youtube import VideoSnapshot
from app.services.analytics.clock import AgeClock
from app.services.analytics.settings import AgeSettings


@dataclass(frozen=True, slots=True)
class CleanSnapshot:
    captured_at: datetime
    age_days: float
    views: int
    likes: int | None
    comments: int | None


@dataclass(frozen=True, slots=True)
class CleanSeries:
    points: tuple[CleanSnapshot, ...]
    count_decreased: bool
    frozen_intervals: tuple[tuple[datetime, datetime], ...]

    @property
    def latest(self) -> CleanSnapshot | None:
        return self.points[-1] if self.points else None

    @property
    def first(self) -> CleanSnapshot | None:
        return self.points[0] if self.points else None

    def is_frozen_between(self, start: datetime, end: datetime) -> bool:
        return any(lo < end and start < hi for lo, hi in self.frozen_intervals)


def _suffix_minimum(values: Sequence[int | None]) -> list[int | None]:
    # later counts are the audited ones, so each value is capped by every later value
    corrected: list[int | None] = list(values)
    running: int | None = None
    for index in range(len(values) - 1, -1, -1):
        value = values[index]
        if value is None:
            continue
        running = value if running is None else min(running, value)
        corrected[index] = running
    return corrected


class SnapshotCleaner:
    def __init__(self, settings: AgeSettings) -> None:
        self.settings = settings

    def clean(self, snapshots: Sequence[VideoSnapshot], anchor: datetime, as_of: datetime) -> CleanSeries:
        usable = sorted(
            (snap for snap in snapshots if anchor <= snap.captured_at <= as_of),
            key=lambda snap: snap.captured_at,
        )
        usable = self._drop_near_duplicates(usable)
        views = _suffix_minimum([snap.view_count for snap in usable])
        likes = _suffix_minimum([snap.like_count for snap in usable])
        comments = _suffix_minimum([snap.comment_count for snap in usable])
        decreased = any(
            raw.view_count - (fixed or 0) >= max(0.01 * raw.view_count, 10)
            for raw, fixed in zip(usable, views, strict=True)
        )
        points = tuple(
            CleanSnapshot(
                captured_at=snap.captured_at,
                age_days=AgeClock.age_between(anchor, snap.captured_at),
                views=int(view),
                likes=like,
                comments=comment,
            )
            for snap, view, like, comment in zip(usable, views, likes, comments, strict=True)
            if view is not None
        )
        return CleanSeries(points=points, count_decreased=decreased, frozen_intervals=self._frozen(points))

    def _drop_near_duplicates(self, snapshots: list[VideoSnapshot]) -> list[VideoSnapshot]:
        window = timedelta(minutes=self.settings.snapshot_dedupe_minutes)
        kept: list[VideoSnapshot] = []
        for snap in snapshots:
            if kept and snap.captured_at - kept[-1].captured_at <= window:
                kept[-1] = snap
            else:
                kept.append(snap)
        return kept

    def _frozen(self, points: tuple[CleanSnapshot, ...]) -> tuple[tuple[datetime, datetime], ...]:
        freeze = timedelta(hours=self.settings.freeze_hours)
        intervals: list[tuple[datetime, datetime]] = []
        start = 0
        while start < len(points):
            end = start
            while end + 1 < len(points) and points[end + 1].views == points[start].views:
                end += 1
            run_start, run_end = points[start], points[end]
            if (
                run_end.captured_at - run_start.captured_at >= freeze
                and run_start.age_days < self.settings.freeze_max_age_days
                and self._grew_before(points, start, freeze)
            ):
                intervals.append((run_start.captured_at, run_end.captured_at))
            start = end + 1
        return tuple(intervals)

    @staticmethod
    def _grew_before(points: tuple[CleanSnapshot, ...], index: int, span: timedelta) -> bool:
        current = points[index]
        earlier = [p for p in points[:index] if current.captured_at - p.captured_at <= span]
        return bool(earlier) and earlier[0].views < current.views
