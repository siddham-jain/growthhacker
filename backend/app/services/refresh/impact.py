from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from statistics import median

from app.core.config import RefreshSettings
from app.domain.analytics import MetricFlag
from app.domain.refresh import RefreshImpact, RefreshStatus
from app.domain.youtube import OwnVideoDayViews, VideoSnapshot
from app.services.refresh.eligibility import VideoState
from app.services.refresh.inputs import StoredRefresh

CAVEATS = (
    "Older videos naturally lose views over time.",
    "News itself raises searches for the topic, whether or not the description changed.",
    "Seasonal events can move views.",
    "YouTube changes its systems from time to time.",
    "Small numbers swing a lot.",
    "This shows what happened after the edit, not proof the edit caused it.",
)
SNAPSHOT_TOLERANCE = timedelta(days=2)
SPIKE_FACTOR = 3.0
CONFOUNDING_FLAGS = frozenset({MetricFlag.title_changed, MetricFlag.thumbnail_changed})
APPLIED = frozenset({RefreshStatus.applied_claimed, RefreshStatus.applied_detected})


def views_at(snapshots: Sequence[VideoSnapshot], at: datetime) -> float | None:
    """linear interpolation between the snapshots around `at`, each within two days of it."""
    before = [
        item for item in snapshots if item.captured_at <= at and at - item.captured_at <= SNAPSHOT_TOLERANCE
    ]
    after = [
        item for item in snapshots if item.captured_at >= at and item.captured_at - at <= SNAPSHOT_TOLERANCE
    ]
    if not before or not after:
        return None
    low = max(before, key=lambda item: item.captured_at)
    high = min(after, key=lambda item: item.captured_at)
    span = (high.captured_at - low.captured_at).total_seconds()
    if span == 0:
        return float(low.view_count)
    share = (at - low.captured_at).total_seconds() / span
    return low.view_count + share * (high.view_count - low.view_count)


def _velocity(snapshots: Sequence[VideoSnapshot], start: datetime, end: datetime) -> float | None:
    first, last = views_at(snapshots, start), views_at(snapshots, end)
    if first is None or last is None:
        return None
    return max(0.0, last - first) / ((end - start).total_seconds() / 86_400)


class ImpactCalculator:
    """views/day 14 days before vs after, against the client's own comparable unrefreshed videos."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def due(self, stored: StoredRefresh, now: datetime) -> bool:
        if stored.refresh.status not in APPLIED or stored.applied_at is None:
            return False
        return now.date() >= stored.applied_at + timedelta(days=self.settings.impact_window_days)

    def compute(
        self,
        stored: StoredRefresh,
        *,
        states: Mapping[str, VideoState],
        snapshots: Mapping[str, Sequence[VideoSnapshot]],
        history: Sequence[StoredRefresh],
        owner_search_daily: Mapping[str, Sequence[OwnVideoDayViews]],
        now: datetime,
    ) -> RefreshImpact:
        settings = self.settings
        refresh = stored.refresh
        applied_on = stored.applied_at or refresh.generated_at.date()
        window = timedelta(days=settings.impact_window_days)
        t0 = datetime.combine(applied_on, time(), tzinfo=UTC)
        own = snapshots.get(refresh.video_id, ())
        pre = _velocity(own, t0 - window, t0)
        post = _velocity(own, t0, t0 + window)
        subject = states.get(refresh.video_id)
        floor = subject.floor if subject else settings.velocity_floor_abs
        control = self._control_ratios(refresh.video_id, t0, states, snapshots, history)
        control_ratio = median(control) if len(control) >= settings.impact_control_min else None
        ratio = (post / pre) if pre and post is not None else 0.0
        adjusted = ratio / control_ratio if control_ratio else None
        verdict = self._verdict(
            pre, post, floor, control_ratio, adjusted, self._confounded(stored, t0, own, subject, history)
        )
        search_pre, search_post = self._owner_search(owner_search_daily.get(refresh.video_id), applied_on)
        return RefreshImpact(
            refresh_id=refresh.id,
            applied_at=applied_on,
            applied_source=stored.applied_source or "user",
            pre_window_days=settings.impact_window_days,
            post_window_days=settings.impact_window_days,
            pre_velocity=round(pre or 0.0, 3),
            post_velocity=round(post or 0.0, 3),
            ratio=round(ratio, 4),
            control_ratio=round(control_ratio, 4) if control_ratio else None,
            adjusted_ratio=round(adjusted, 4) if adjusted else None,
            owner_search_views_pre=search_pre,
            owner_search_views_post=search_post,
            verdict=verdict,
            caveats=list(CAVEATS),
            computed_at=now,
        )

    def _verdict(
        self,
        pre: float | None,
        post: float | None,
        floor: float,
        control_ratio: float | None,
        adjusted: float | None,
        confounded: bool,
    ) -> str:
        if pre is None or post is None or pre < floor or control_ratio is None or adjusted is None:
            return "insufficient_data"
        if confounded:
            return "confounded"
        if adjusted >= self.settings.impact_up_ratio:
            return "up"
        if adjusted <= self.settings.impact_down_ratio:
            return "down"
        return "no_clear_change"

    def _control_ratios(
        self,
        video_id: str,
        t0: datetime,
        states: Mapping[str, VideoState],
        snapshots: Mapping[str, Sequence[VideoSnapshot]],
        history: Sequence[StoredRefresh],
    ) -> list[float]:
        settings = self.settings
        subject = states.get(video_id)
        if subject is None:
            return []
        window = timedelta(days=settings.impact_window_days)
        gap = timedelta(days=settings.impact_control_refresh_gap_days)
        subject_age = (t0 - subject.video.effective_published_at).total_seconds() / 86_400
        tolerance = settings.impact_control_age_tolerance
        refreshed = {
            item.refresh.video_id
            for item in history
            if item.applied_at is not None
            and abs(datetime.combine(item.applied_at, time(), tzinfo=UTC) - t0) <= gap
        }
        ratios = []
        for state in states.values():
            if state.video_id == video_id or state.video_id in refreshed or not state.is_public:
                continue
            if state.video.format is not subject.video.format:
                continue
            age = (t0 - state.video.effective_published_at).total_seconds() / 86_400
            if not (1 - tolerance) * subject_age <= age <= (1 + tolerance) * subject_age:
                continue
            series = snapshots.get(state.video_id, ())
            pre, post = _velocity(series, t0 - window, t0), _velocity(series, t0, t0 + window)
            if pre is None or post is None or pre < state.floor:
                continue
            ratios.append(post / pre)
        return ratios

    def _confounded(
        self,
        stored: StoredRefresh,
        t0: datetime,
        series: Sequence[VideoSnapshot],
        subject: VideoState | None,
        history: Sequence[StoredRefresh],
    ) -> bool:
        window = timedelta(days=self.settings.impact_window_days)
        for item in history:
            if item.refresh.id == stored.refresh.id or item.refresh.video_id != stored.refresh.video_id:
                continue
            if (
                item.applied_at is not None
                and abs(datetime.combine(item.applied_at, time(), tzinfo=UTC) - t0) <= window
            ):
                return True
        if (
            subject is not None
            and subject.metrics is not None
            and CONFOUNDING_FLAGS & set(subject.metrics.flags)
        ):
            return True
        half = window / 2
        early, late = _velocity(series, t0 - window, t0 - half), _velocity(series, t0 - half, t0)
        if early is not None and late is not None and min(early, late) > 0:
            return max(early, late) / min(early, late) >= SPIKE_FACTOR
        return False

    def _owner_search(
        self, days: Sequence[OwnVideoDayViews] | None, applied_on: date
    ) -> tuple[int | None, int | None]:
        if not days:
            return None, None
        window = timedelta(days=self.settings.impact_window_days)
        pre = sum(day.views for day in days if applied_on - window <= day.day < applied_on)
        post = sum(day.views for day in days if applied_on <= day.day < applied_on + window)
        return pre, post
