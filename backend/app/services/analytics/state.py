from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.domain.analytics import (
    TRUE_AGE_METHODS,
    AgeMethod,
    ChannelBaseline,
    MetricFlag,
    OutlierAssessment,
    OutlierTier,
    Velocity,
    VideoMetrics,
    ViewsAtAge,
)
from app.domain.common import ChannelRole, FormatSegment, format_key
from app.domain.youtube import OwnAnalytics, VideoRecord, VideoSnapshot
from app.services.analytics.ages import DEFAULT_ORDER, AgeContext, OwnerDailyViews, ViewsAtAgeResolver
from app.services.analytics.baselines import (
    TRUE_FAMILIES,
    AgeFamily,
    AgeValue,
    BaselineBuilder,
    BuiltBaseline,
    VideoFacts,
    family_of,
)
from app.services.analytics.clock import AgeClock
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.outliers import OutlierAssessor
from app.services.analytics.rates import Rates, compute_rates, recent_velocity, week_velocity
from app.services.analytics.segments import (
    DuplicateCandidate,
    DuplicateCollapser,
    length_band,
    segment_evidence,
    segment_of,
)
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.snapshots import CleanSeries, SnapshotCleaner

METRICS_VERSION = "metrics-2026.11.1"
PASSTHROUGH_FLAGS = {
    "title_changed": MetricFlag.title_changed,
    "thumbnail_changed": MetricFlag.thumbnail_changed,
    "published_at_changed": MetricFlag.published_at_changed,
}


@dataclass
class VideoState:
    video: VideoRecord
    role: ChannelRole
    segment: FormatSegment
    length_band: str
    anchor: datetime
    age_now: float
    series: CleanSeries
    rates: Rates
    ages: dict[int, ViewsAtAge]
    data_api_ages: dict[int, ViewsAtAge]
    velocity_recent: Velocity | None
    velocity_7d: Velocity | None
    flags: set[MetricFlag] = field(default_factory=set)
    duplicate_group_id: str | None = None
    duplicate_secondary: bool = False
    cross_duplicate_group: str | None = None
    facts: VideoFacts | None = None

    @property
    def video_id(self) -> str:
        return self.video.video_id

    @property
    def channel_id(self) -> str:
        return self.video.channel_id

    @property
    def published_at(self) -> datetime:
        return self.video.published_at

    @property
    def views_now(self) -> int:
        return self.series.latest.views

    def ordering_key(self) -> tuple[datetime, str]:
        return (self.published_at, self.video_id)


def _owner_daily(analytics: OwnAnalytics | None, video_id: str) -> OwnerDailyViews | None:
    if analytics is None or analytics.connection.status != "active":
        return None
    days = {row.day: row.views for row in analytics.video_daily_views if row.video_id == video_id}
    return OwnerDailyViews(
        days=days, data_through=analytics.data_through, verified_at=analytics.connection.last_verified_at
    )


def _with_metadata_point(
    snapshots: list[VideoSnapshot], video: VideoRecord, as_of: datetime
) -> list[VideoSnapshot]:
    # a videos.list count is a real observation; it is used only when no snapshot exists yet
    if snapshots or video.metadata_fetched_at > as_of:
        return snapshots
    return [
        VideoSnapshot(
            video_id=video.video_id,
            slot_at=video.metadata_fetched_at,
            captured_at=video.metadata_fetched_at,
            age_hours=max(0.0, (video.metadata_fetched_at - video.published_at).total_seconds() / 3600),
            view_count=video.view_count,
            like_count=video.like_count,
            comment_count=video.comment_count,
            source="videos_list",
            counting_regime="standard",
        )
    ]


class StateBuilder:
    def __init__(self, settings: AnalyticsEngineSettings, as_of: datetime) -> None:
        self.settings = settings
        self.as_of = as_of
        self.clock = AgeClock(as_of)
        self.cleaner = SnapshotCleaner(settings.ages)
        self.resolver = ViewsAtAgeResolver(settings.ages)
        self.data_api_resolver = ViewsAtAgeResolver(
            settings.ages, tuple(k for k in DEFAULT_ORDER if k != "owner_analytics")
        )

    def build(self, inputs: AnalyticsInputs) -> tuple[dict[str, VideoState], dict[str, str]]:
        roles = {c.channel.channel_id: c.role for c in inputs.channels}
        states: dict[str, VideoState] = {}
        excluded: dict[str, str] = {}
        for video in sorted(inputs.videos, key=lambda v: v.video_id):
            reason = self._exclusion(video)
            if reason:
                excluded[video.video_id] = reason
                continue
            state = self._state(video, roles.get(video.channel_id, ChannelRole.niche), inputs)
            if state is None:
                excluded[video.video_id] = "no_statistics"
                continue
            states[video.video_id] = state
        self._mark_duplicates(states)
        for state in states.values():
            state.facts = self._facts(state)
        return states, excluded

    @staticmethod
    def _exclusion(video: VideoRecord) -> str | None:
        if video.availability != "available":
            return "unavailable_now"
        if video.live_broadcast_content == "upcoming":
            return "upcoming"
        if video.live_broadcast_content == "live":
            return "live_now"
        if segment_of(video) is None:
            return "not_yet_processed"
        return None

    def _state(self, video: VideoRecord, role: ChannelRole, inputs: AnalyticsInputs) -> VideoState | None:
        segment = segment_of(video)
        anchor = self.clock.anchor(video)
        snapshots = _with_metadata_point(list(inputs.snapshots.get(video.video_id, [])), video, self.as_of)
        series = self.cleaner.clean(snapshots, anchor, self.as_of)
        latest = series.latest
        if latest is None:
            return None
        age_now = self.clock.age_between(anchor, self.as_of)
        owner = (
            _owner_daily(inputs.own_analytics, video.video_id)
            if video.channel_id == inputs.own_channel_id
            else None
        )
        ctx = AgeContext(video, series, anchor, self.as_of, age_now, self.settings.ages, owner)
        ages = self.resolver.resolve(ctx)
        data_api_ages = self.data_api_resolver.resolve(ctx) if owner else ages
        state = VideoState(
            video=video,
            role=role,
            segment=segment,
            length_band=length_band(segment, video.duration_s),
            anchor=anchor,
            age_now=age_now,
            series=series,
            rates=compute_rates(latest.views, latest.likes, latest.comments, self.settings.rates),
            ages=ages,
            data_api_ages=data_api_ages,
            velocity_recent=recent_velocity(series, self.settings.rates),
            velocity_7d=week_velocity(series, self.settings.rates),
        )
        state.flags |= self._flags(state, latest.likes, latest.comments)
        return state

    def _flags(self, state: VideoState, likes: int | None, comments: int | None) -> set[MetricFlag]:
        video = state.video
        flags = {PASSTHROUGH_FLAGS[f] for f in video.quality_flags if f in PASSTHROUGH_FLAGS}
        if likes is None:
            flags.add(MetricFlag.likes_hidden)
        if comments is None or video.comments_state == "disabled" or video.made_for_kids:
            flags.add(MetricFlag.comments_disabled)
        if state.series.count_decreased:
            flags.add(MetricFlag.count_decreased)
        if state.series.frozen_intervals:
            flags.add(MetricFlag.count_frozen)
        if state.rates.low_views:
            flags.add(MetricFlag.low_views_for_rates)
        if state.segment is FormatSegment.live:
            flags.add(MetricFlag.live_or_premiere)
        if self._old_shorts_regime(state):
            flags.add(MetricFlag.shorts_counting_regime_pre_2025_03_31)
        return flags

    def _old_shorts_regime(self, state: VideoState) -> bool:
        cutoff = self.settings.segments.shorts_regime_cutoff
        return state.segment is FormatSegment.short_form and state.published_at.date() < cutoff

    def _mark_duplicates(self, states: dict[str, VideoState]) -> None:
        candidates = [
            DuplicateCandidate(
                video_id=s.video_id,
                channel_id=s.channel_id,
                title=s.video.title,
                duration_s=s.video.duration_s or 0,
                published_at=s.published_at,
                rank_views=s.views_now,
            )
            for s in states.values()
        ]
        groups = DuplicateCollapser(self.settings.segments).collapse(candidates)
        for video_id, state in states.items():
            state.duplicate_group_id = groups.group_of.get(video_id)
            state.duplicate_secondary = groups.is_secondary(video_id)
            state.cross_duplicate_group = groups.cross_channel_group_of.get(video_id)
            if state.duplicate_secondary:
                state.flags.add(MetricFlag.duplicate_of)
            if state.cross_duplicate_group:
                state.flags.add(MetricFlag.cross_channel_duplicate)

    def _facts(self, state: VideoState) -> VideoFacts:
        values: dict[tuple[int, AgeFamily], AgeValue] = {}
        latest = state.series.latest
        for age in self.settings.ages.target_ages_days:
            for resolved in (state.ages.get(age), state.data_api_ages.get(age)):
                family = family_of(resolved.method) if resolved else None
                if family in TRUE_FAMILIES and resolved.views is not None:
                    values[(age, family)] = AgeValue(
                        resolved.views,
                        resolved.method,
                        resolved.observed_age_days or age,
                        resolved.source_oldest_at,
                    )
            if latest.age_days >= age:
                values[(age, AgeFamily.proxy)] = AgeValue(
                    latest.views, AgeMethod.age_band_proxy, latest.age_days, latest.captured_at
                )
        return VideoFacts(
            video_id=state.video_id,
            channel_id=state.channel_id,
            segment=state.segment,
            published_at=state.published_at,
            language=state.video.default_audio_language,
            baseline_eligible=not state.duplicate_secondary and not self._old_shorts_regime(state),
            series=state.series,
            views_now=latest.views,
            latest_age_days=latest.age_days,
            values=values,
            like_rate=state.rates.like_rate,
            engagement_rate=state.rates.engagement_rate,
            engagement_basis=state.rates.basis,
        )


class AssessmentBook:
    """same-family numerator and baseline, always; true families fall back to proxy only when sufficient."""

    MAX_SPIKE_PASSES = 4

    def __init__(
        self, states: Iterable[VideoState], settings: AnalyticsEngineSettings, as_of: datetime
    ) -> None:
        self.settings = settings
        self.as_of = as_of
        self.assessor = OutlierAssessor(settings)
        self.states = sorted(states, key=VideoState.ordering_key)
        self.spikes: frozenset[str] = frozenset()
        self._reset()
        # suppressed spikes never define "normal": drop them from baselines and reassess until stable
        for _ in range(self.MAX_SPIKE_PASSES):
            found = self._suppressed_spikes()
            if found == self.spikes:
                break
            self.spikes = found
            self._reset()

    def _reset(self) -> None:
        facts = [
            replace(s.facts, baseline_eligible=s.facts.baseline_eligible and s.video_id not in self.spikes)
            for s in self.states
        ]
        self.builder = BaselineBuilder(facts, self.settings, self.as_of)
        self._facts = {f.video_id: f for f in facts}
        self._assessed: dict[tuple[str, int, AgeFamily], OutlierAssessment | None] = {}
        self._like_rates: dict[tuple[str, FormatSegment], ChannelBaseline] = {}
        self.baselines: dict[str, BuiltBaseline] = {}

    def _suppressed_spikes(self) -> frozenset[str]:
        return frozenset(
            s.video_id
            for s in self.states
            for age in self.settings.comparison_ages()
            if (a := self.best(s, age)) is not None and a.suppressed_reason == "views_without_engagement"
        )

    def facts_of(self, video_id: str) -> VideoFacts:
        return self._facts[video_id]

    def like_rate(self, channel_id: str, segment: FormatSegment) -> ChannelBaseline:
        key = (channel_id, segment)
        if key not in self._like_rates:
            self._like_rates[key] = self.builder.rate_baseline(channel_id, segment, "like_rate")
        return self._like_rates[key]

    def assess(self, state: VideoState, age: int, family: AgeFamily) -> OutlierAssessment | None:
        key = (state.video_id, age, family)
        if key not in self._assessed:
            facts = self._facts[state.video_id]
            value = facts.value(age, family)
            if value is None:
                self._assessed[key] = None
            else:
                built = self.builder.for_video(facts, age, family)
                self.baselines[built.baseline.baseline_id] = built
                self._assessed[key] = self.assessor.assess(
                    facts,
                    age,
                    value,
                    built.baseline,
                    self.like_rate(state.channel_id, state.segment),
                    state.duplicate_secondary,
                )
        return self._assessed[key]

    def true_assessment(self, state: VideoState, age: int) -> OutlierAssessment | None:
        for family in TRUE_FAMILIES:
            assessed = self.assess(state, age, family)
            if assessed is not None:
                return assessed
        return None

    def best(self, state: VideoState, age: int) -> OutlierAssessment | None:
        true = self.true_assessment(state, age)
        if true is not None and true.tier is not OutlierTier.insufficient:
            return true
        proxy = self.assess(state, age, AgeFamily.proxy)
        if proxy is not None and proxy.tier is not OutlierTier.insufficient:
            return proxy
        return true or proxy

    def baseline_of(self, assessment: OutlierAssessment) -> BuiltBaseline:
        return self.baselines[assessment.baseline_id]


def has_true_value(state: VideoState, age: int) -> bool:
    resolved = state.ages.get(age)
    return resolved is not None and resolved.method in TRUE_AGE_METHODS


def metric_flags(
    state: VideoState, age: int, assessment: OutlierAssessment | None, book: AssessmentBook
) -> list[MetricFlag]:
    flags = set(state.flags)
    resolved = state.ages.get(age)
    if resolved is not None and resolved.method is AgeMethod.pending:
        flags.add(MetricFlag.too_new)
    if assessment is not None:
        built = book.baseline_of(assessment)
        if (
            built.baseline.fallback_level == "non_trailing"
            and assessment.method is not AgeMethod.age_band_proxy
        ):
            flags.add(MetricFlag.non_trailing_baseline)
        if built.baseline.fallback_level == "widened" or built.widened:
            flags.add(MetricFlag.widened_lookback)
        if assessment.suppressed_reason == "views_without_engagement":
            flags.add(MetricFlag.views_without_engagement)
        if assessment.suppressed_reason == "tiny_baseline":
            flags.add(MetricFlag.tiny_baseline)
    return sorted(flags, key=lambda f: f.value)


def build_metrics(
    state: VideoState, age: int, book: AssessmentBook, as_of: datetime, extra_flags: Iterable[MetricFlag] = ()
) -> VideoMetrics:
    assessment = book.best(state, age)
    latest = state.series.latest
    return VideoMetrics(
        video_id=state.video_id,
        channel_id=state.channel_id,
        as_of=as_of,
        segment=state.segment,
        segment_evidence=segment_evidence(state.video),
        format_key=format_key(state.segment, state.length_band),
        age_anchor_at=state.anchor,
        age_days=state.age_now,
        views_now=latest.views,
        likes_now=latest.likes,
        comments_now=latest.comments,
        views_at_age=state.ages,
        comparison_age_days=age,
        views_per_day_lifetime=latest.views / max(state.age_now, 1.0),
        velocity_recent=state.velocity_recent,
        velocity_7d=state.velocity_7d,
        engagement_rate=state.rates.engagement_rate,
        like_rate=state.rates.like_rate,
        comment_rate=state.rates.comment_rate,
        engagement_basis=state.rates.basis,
        outlier=assessment,
        duplicate_group_id=state.duplicate_group_id,
        flags=sorted(
            set(metric_flags(state, age, assessment, book)) | set(extra_flags), key=lambda f: f.value
        ),
        metrics_version=METRICS_VERSION,
    )
