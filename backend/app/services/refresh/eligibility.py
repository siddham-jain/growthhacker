from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal
from uuid import UUID

from app.core.config import RefreshSettings
from app.domain.analytics import ChannelBaseline, VideoMetrics
from app.domain.common import DomainModel, VideoFormat, VideoId
from app.domain.news import NewsSignal
from app.domain.profile import FreshnessProfile, OperatingLevel
from app.domain.refresh import RefreshEligibility
from app.domain.youtube import VideoRecord
from app.services.refresh.inputs import RefreshClientOptions, StoredRefresh

VelocityBasis = Literal["snapshots", "lifetime_avg_provisional"]
Decision = Literal["none", "hygiene_only", "periodic", "active"]
DECISION_ORDER: tuple[Decision, ...] = ("none", "hygiene_only", "periodic", "active")
NEWS_PRIOR: dict[OperatingLevel, float] = {
    OperatingLevel.high: 0.9,
    OperatingLevel.medium: 0.5,
    OperatingLevel.low: 0.1,
    OperatingLevel.none: 0.0,
}
APPLIED_STATUSES = frozenset({"applied_claimed", "applied_detected"})


@dataclass(frozen=True, slots=True)
class VideoState:
    video: VideoRecord
    metrics: VideoMetrics | None
    age_days: float
    velocity: float | None
    velocity_basis: VelocityBasis
    floor: float
    shorts_max_duration_s: int

    @property
    def video_id(self) -> str:
        return self.video.video_id

    @property
    def is_public(self) -> bool:
        video = self.video
        return (
            video.privacy_status == "public"
            and video.availability == "available"
            and video.live_broadcast_content == "none"
            and video.format is not VideoFormat.upcoming
        )

    @property
    def earning(self) -> bool:
        return self.velocity is not None and self.velocity >= self.floor

    @property
    def is_short(self) -> bool:
        return self.video.format is VideoFormat.short_form

    @property
    def maybe_short(self) -> bool:
        # 61-180 s without aspect data is treated as a short for links, conservatively
        duration = self.video.duration_s
        return self.is_short or (
            duration is not None and duration <= self.shorts_max_duration_s and self.video.aspect == "unknown"
        )

    @property
    def is_event_video(self) -> bool:
        return self.video.live_actual_start_at is not None or self.video.format is VideoFormat.live


def recent_velocity(metrics: VideoMetrics, min_hours: float) -> tuple[float, VelocityBasis]:
    for velocity in (metrics.velocity_7d, metrics.velocity_recent):
        if velocity is not None and velocity.window_hours >= min_hours:
            return velocity.views_per_day, "snapshots"
    return metrics.views_per_day_lifetime, "lifetime_avg_provisional"


def _baseline_median(baselines: Sequence[ChannelBaseline], channel_id: str, age_days: float) -> float | None:
    for baseline in baselines:
        if baseline.metric != "velocity" or baseline.channel_id != channel_id or baseline.age_band is None:
            continue
        low, high = baseline.age_band
        if low <= age_days < high and baseline.sufficient:
            return baseline.median
    return None


def build_video_states(
    videos: Sequence[VideoRecord],
    metrics: Sequence[VideoMetrics],
    baselines: Sequence[ChannelBaseline],
    now: datetime,
    settings: RefreshSettings,
) -> list[VideoState]:
    by_id = {item.video_id: item for item in metrics}
    measured: list[tuple[VideoRecord, VideoMetrics | None, float, float | None, VelocityBasis]] = []
    for video in videos:
        item = by_id.get(video.video_id)
        age = item.age_days if item else (now - video.effective_published_at).total_seconds() / 86_400
        velocity, basis = (
            recent_velocity(item, settings.snapshot_velocity_min_hours)
            if item
            else (None, "lifetime_avg_provisional")
        )
        measured.append((video, item, max(age, 0.0), velocity, basis))
    velocities = [(age, velocity) for _, _, age, velocity, _ in measured if velocity is not None]
    states = []
    for video, item, age, velocity, basis in measured:
        band = _baseline_median(baselines, video.channel_id, age)
        if band is None:
            peers = [value for peer_age, value in velocities if age / 2 <= peer_age <= max(age * 2, 1.0)]
            pool = peers or [value for _, value in velocities]
            band = median(pool) if pool else 0.0
        floor = max(settings.velocity_floor_abs, settings.velocity_floor_rel * band)
        states.append(
            VideoState(
                video=video,
                metrics=item,
                age_days=age,
                velocity=velocity,
                velocity_basis=basis,
                floor=floor,
                shorts_max_duration_s=settings.shorts_max_duration_s,
            )
        )
    return states


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligibility: RefreshEligibility
    keyword_checks_due: bool
    news_relevance_min: float
    review_required: bool

    @property
    def decision(self) -> Decision:
        return self.eligibility.decision

    @property
    def keyword_enabled(self) -> bool:
        return self.decision in ("periodic", "active") and self.keyword_checks_due

    @property
    def provisional(self) -> bool:
        return self.eligibility.velocity_basis == "lifetime_avg_provisional"


def relevant_signals(
    signals: Sequence[NewsSignal], now: datetime, settings: RefreshSettings
) -> list[NewsSignal]:
    since = now - timedelta(days=settings.news_lookback_days)
    return [
        signal
        for signal in signals
        if signal.verification_status == "active"
        and signal.relevance_score >= settings.news_relevance_min
        and signal.first_seen_at >= since
    ]


def _cap(decision: Decision, ceiling: Decision) -> Decision:
    return min(decision, ceiling, key=DECISION_ORDER.index)


class BusinessEligibilityEvaluator:
    """decides per business whether keyword refreshes are worth proposing at all, and how often."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def evaluate(
        self,
        *,
        client_id: UUID,
        run_id: UUID,
        now: datetime,
        freshness: FreshnessProfile,
        states: Sequence[VideoState],
        signals: Sequence[NewsSignal],
        buyer_gap_video_ids: Collection[str],
        defect_video_ids: Collection[str],
        options: RefreshClientOptions,
        last_keyword_eval_at: datetime | None,
    ) -> EligibilityDecision:
        settings = self.settings
        relevant = relevant_signals(signals, now, settings)
        prior = NEWS_PRIOR[freshness.operating_level]
        news_component = 0.5 * prior + 0.5 * min(1.0, len(relevant) / settings.news_target)
        public = [state for state in states if state.is_public and state.velocity is not None]
        earning = [state for state in public if state.earning]
        evergreen = [state for state in earning if state.age_days >= settings.evergreen_min_age_days]
        total_velocity = sum(state.velocity or 0.0 for state in public)
        evergreen_share = (
            sum(state.velocity or 0.0 for state in evergreen) / total_velocity if total_velocity else 0.0
        )
        provisional = sum(state.velocity_basis == "snapshots" for state in public) * 2 < len(public)
        basis: Literal["snapshots", "lifetime_avg_provisional"] = (
            "lifetime_avg_provisional" if provisional else "snapshots"
        )
        weighted_share = evergreen_share * (settings.provisional_share_multiplier if provisional else 1.0)
        buyer_gap_rate = (
            sum(state.video_id in buyer_gap_video_ids for state in evergreen) / len(evergreen)
            if evergreen
            else 0.0
        )
        defect_rate = (
            sum(state.video_id in defect_video_ids for state in earning) / len(earning) if earning else 0.0
        )
        weights = settings.need_weights
        need = (
            weights.news * news_component
            + weights.evergreen * weighted_share
            + weights.buyer_gap * buyer_gap_rate
        )
        need = min(1.0, max(0.0, need))

        reasons = [self._news_reason(relevant), self._evergreen_reason(public, evergreen, evergreen_share)]
        if not earning:
            decision: Decision = "none"
            reasons.append("Your videos aren't earning steady views right now, so edits won't pay off")
        elif need < settings.hygiene_only_below:
            decision = "hygiene_only"
        elif need < settings.active_from:
            decision = "periodic"
        else:
            decision = "active"
        if decision in ("periodic", "active") and not relevant and buyer_gap_rate == 0:
            decision = "hygiene_only"
            reasons.append("No fresh news and no buyer-question gaps, so there is no keyword worth adding")
        if freshness.regulated_domain and decision == "active":
            decision = "periodic"
        if freshness.regulated_domain:
            reasons.append(
                f"Regulated domain ({freshness.domain_kind}): every suggestion needs review before publishing"
            )
        if provisional and earning:
            decision = _cap(decision, "periodic")
            reasons.append("Based on lifetime averages; we'll confirm once we have 7 days of snapshots")
        if options.mode == "off":
            decision = "none"
            reasons.append("Description refresh is turned off in Settings")
        elif options.mode == "hygiene_only" and decision != "none":
            decision = "hygiene_only"
            reasons.append("Settings limit suggestions to link and booking fixes")
        if decision == "hygiene_only" and need < settings.hygiene_only_below and earning:
            reasons.append("Keyword refreshes aren't needed for this business right now")

        cadence = settings.cadence_days[decision]
        due = self._keyword_checks_due(now, cadence, last_keyword_eval_at, relevant)
        if decision in ("periodic", "active") and not due and last_keyword_eval_at is not None:
            next_check = (last_keyword_eval_at + timedelta(days=cadence)).date()
            reasons.append(f"Keyword checks run every {cadence} days; next check on {next_check.isoformat()}")
        eligibility = RefreshEligibility(
            client_id=client_id,
            run_id=run_id,
            decision=decision,
            cadence_days=cadence,
            keyword_refresh_need=round(need, 4),
            components={
                "news_component": round(news_component, 4),
                "evergreen_view_share": round(evergreen_share, 4),
                "buyer_gap_rate": round(buyer_gap_rate, 4),
                "hygiene_defect_rate": round(defect_rate, 4),
            },
            evergreen_video_count=len(evergreen),
            eligible_video_count=0,
            velocity_basis=basis,
            reasons=reasons,
            computed_at=now,
        )
        news_min = (
            settings.periodic_news_relevance_min if decision == "periodic" else settings.news_relevance_min
        )
        return EligibilityDecision(
            eligibility=eligibility,
            keyword_checks_due=due,
            news_relevance_min=news_min,
            review_required=freshness.regulated_domain,
        )

    def _keyword_checks_due(
        self, now: datetime, cadence: int, last: datetime | None, relevant: Sequence[NewsSignal]
    ) -> bool:
        if last is None or now - last >= timedelta(days=cadence):
            return True
        bypass = self.settings.cadence_bypass_relevance
        return any(signal.relevance_score >= bypass and signal.first_seen_at > last for signal in relevant)

    def _news_reason(self, relevant: Sequence[NewsSignal]) -> str:
        days = self.settings.news_lookback_days
        if not relevant:
            return f"No relevant niche news in the last {days} days"
        plural = "s" if len(relevant) != 1 else ""
        return f"{len(relevant)} relevant news signal{plural} in the last {days} days"

    def _evergreen_reason(
        self, public: Sequence[VideoState], evergreen: Sequence[VideoState], share: float
    ) -> str:
        days = self.settings.evergreen_min_age_days
        return (
            f"{len(evergreen)} of {len(public)} public videos are older than {days} days and still earning "
            f"views ({share:.0%} of daily views, calculated by GrowthHacker)"
        )


class VideoEligibility(DomainModel):
    video_id: VideoId
    public: bool
    earning: bool
    evergreen: bool
    velocity: float | None
    velocity_basis: VelocityBasis
    fit_score: float
    is_short: bool
    keyword_eligible: bool
    hygiene_eligible: bool
    cta_eligible: bool
    skip_reasons: list[str] = []


class VideoGate:
    """hard per-video filters; keyword, hygiene and booking-link eligibility are decided separately."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def evaluate(
        self,
        state: VideoState,
        *,
        now: datetime,
        fit_score: float,
        language_supported: bool,
        decision: EligibilityDecision,
        history: Sequence[StoredRefresh],
        options: RefreshClientOptions,
    ) -> VideoEligibility:
        settings = self.settings
        video = state.video
        reasons: list[str] = []
        if not state.is_public:
            reasons.append("not public, live or upcoming")
        if video.video_id in options.excluded_video_ids:
            reasons.append("excluded in Settings")
        if now - video.metadata_fetched_at > timedelta(days=settings.max_description_age_days):
            reasons.append("description needs a refetch")
        cooldown_since = (now - timedelta(days=settings.cooldown_days)).date()
        if any(
            item.refresh.video_id == video.video_id
            and item.refresh.status in APPLIED_STATUSES
            and item.applied_at is not None
            and item.applied_at >= cooldown_since
            for item in history
        ):
            reasons.append(f"refreshed within the last {settings.cooldown_days} days")
        blocked = bool(reasons)
        evergreen = state.earning and state.age_days >= settings.evergreen_min_age_days
        fits = fit_score >= settings.fit_threshold
        keyword_reasons = []
        if not decision.keyword_enabled:
            keyword_reasons.append("keyword checks not due for this business")
        if not state.earning:
            keyword_reasons.append("not earning steady views")
        if not fits:
            keyword_reasons.append("outside your main topics")
        if not language_supported:
            keyword_reasons.append("description language not supported for wording")
        if state.age_days < settings.news_min_age_days:
            keyword_reasons.append("too new")
        hygiene = not blocked and state.age_days >= settings.hygiene_min_age_days
        cta = hygiene and state.earning and fits and not state.maybe_short and not video.made_for_kids
        return VideoEligibility(
            video_id=video.video_id,
            public=state.is_public,
            earning=state.earning,
            evergreen=evergreen,
            velocity=state.velocity,
            velocity_basis=state.velocity_basis,
            fit_score=round(fit_score, 4),
            is_short=state.is_short,
            keyword_eligible=not blocked and not keyword_reasons,
            hygiene_eligible=hygiene,
            cta_eligible=cta,
            skip_reasons=reasons + (keyword_reasons if not blocked else []),
        )
