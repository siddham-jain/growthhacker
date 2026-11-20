import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.clock import FrozenClock
from app.core.config import RefreshSettings
from app.domain.analytics import AgeMethod, OutlierAssessment, OutlierTier, Velocity, VideoMetrics, ViewsAtAge
from app.domain.common import ChannelRole, FormatSegment, VideoFormat
from app.domain.news import (
    ConfirmationStatus,
    CredibilityAssessment,
    DemandCheck,
    EventType,
    GroundedEntity,
    NewsJudgement,
    NewsSignal,
    NewsSourceRef,
    RelevanceBand,
    SafetyDecision,
    SourceTier,
)
from app.domain.profile import (
    BusinessIdentity,
    BusinessModel,
    BusinessProfile,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    ConversionAction,
    FreshnessProfile,
    FreshnessSensitivity,
    Location,
    LocationSummary,
    Offering,
    ProfileQuality,
    Prominence,
    RefreshPolicy,
)
from app.domain.topics import MatchMethod, TopicKind, TopicMatch, TopicRef
from app.domain.youtube import VideoRecord, VideoSnapshot
from app.services.refresh.engine import RefreshEngine
from app.services.refresh.inputs import ReferenceCandidate, RefreshInputs
from app.services.refresh.links.health import LinkHealthService
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.links.urls import malformation, normalise_url

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
CLIENT_ID = uuid.UUID("01930000-0000-7000-8000-000000000009")
RUN_ID = uuid.UUID("01930000-0000-7000-8000-0000000000a9")
OWN_CHANNEL = "UCownChannel000000000009"
NICHE_CHANNELS = ("UCnicheChannelA000000001", "UCnicheChannelB000000002", "UCnicheChannelC000000003")


def meta() -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.user,
        status=ClaimStatus.confirmed,
        confidence=1.0,
        first_seen_version=1,
        updated_at=NOW,
    )


def video_id(prefix: str, index: int) -> str:
    return f"{prefix}{index:0{11 - len(prefix)}d}"


def topic_id(label: str) -> str:
    return "tr_" + hashlib.sha1(label.encode()).hexdigest()[:12]


def freshness(
    sensitivity: FreshnessSensitivity,
    *,
    regulated: bool = False,
    domain_kind: str = "none",
) -> FreshnessProfile:
    return FreshnessProfile(
        sensitivity=sensitivity,
        sensitivity_score=0.5,
        score_components={},
        rationale="fixture",
        regulated_domain=regulated,
        domain_kind=domain_kind,
        refresh_policy=RefreshPolicy(
            news_scan_interval_hours=24,
            refresh_check_interval_days=14,
            description_refresh_eligible=True,
            min_signal_strength=0.5,
        ),
        meta=meta(),
    )


def action(
    action_id: str,
    target: str,
    *,
    kind: str = "book_appointment",
    label: str = "Book an appointment",
    medium: str = "web",
    offering_ids: Sequence[str] = (),
    location_ids: Sequence[str] = (),
    primary: bool = False,
) -> ConversionAction:
    return ConversionAction(
        id=action_id,
        type=kind,
        label=label,
        target=target,
        medium=medium,
        prominence=Prominence(pages_seen=3, on_home=True, in_header=True, in_footer=False),
        is_primary=primary,
        offering_ids=list(offering_ids),
        location_ids=list(location_ids),
        meta=meta(),
    )


def profile(
    *,
    name: str,
    domain: str,
    sensitivity: FreshnessSensitivity,
    business_model: BusinessModel = BusinessModel.b2c_app,
    geo_scope: str = "global_online",
    regulated: bool = False,
    domain_kind: str = "none",
    actions: Sequence[ConversionAction] = (),
    offerings: Sequence[Offering] = (),
    locations: Sequence[Location] = (),
    country: str | None = None,
) -> BusinessProfile:
    return BusinessProfile(
        profile_id=uuid.uuid4(),
        client_id=CLIENT_ID,
        version=1,
        status="confirmed",
        generated_at=NOW,
        identity=BusinessIdentity(
            name=name,
            name_meta=meta(),
            one_liner=f"{name} fixture",
            one_liner_meta=meta(),
            industry="fixture",
            industry_meta=meta(),
            niche="fixture",
            business_model=business_model,
            geo_scope=geo_scope,
            primary_language="en",
            languages=["en"],
            country=country,
            website=None,
            website_domains=[domain],
        ),
        offerings=list(offerings),
        locations=LocationSummary(count=len(locations), items=list(locations)),
        conversion_actions=list(actions),
        freshness=freshness(sensitivity, regulated=regulated, domain_kind=domain_kind),
        quality=ProfileQuality(
            claims_total=1,
            claims_verified=1,
            claims_dropped_unverified=0,
            injection_flags=0,
            thin_site=False,
            label="adequate",
            completeness=0.8,
        ),
    )


def offering(offering_id: str, name: str) -> Offering:
    return Offering(id=offering_id, name=name, kind="service", meta=meta())


def location(location_id: str, name: str, city: str, aliases: Sequence[str] = ()) -> Location:
    return Location(id=location_id, name=name, kind="hospital", city=city, aliases=list(aliases), meta=meta())


def video(
    vid: str,
    *,
    title: str,
    description: str,
    published_days_ago: float,
    channel_id: str = OWN_CHANNEL,
    channel_title: str = "Own channel",
    duration_s: int = 600,
    fmt: VideoFormat = VideoFormat.long_form,
    view_count: int = 50_000,
    tags: Sequence[str] = (),
    privacy: str = "public",
    live: str = "none",
    default_language: str | None = "en",
    localizations: dict | None = None,
) -> VideoRecord:
    published = NOW - timedelta(days=published_days_ago)
    return VideoRecord(
        video_id=vid,
        channel_id=channel_id,
        channel_title=channel_title,
        published_at=published,
        effective_published_at=published,
        live_broadcast_content=live,
        title=title,
        description=description,
        tags=list(tags),
        content_hash=hashlib.sha1((title + description).encode()).hexdigest(),
        description_hash=hashlib.sha1(description.encode()).hexdigest(),
        default_language=default_language,
        localizations=localizations or {},
        duration_s=duration_s,
        format=fmt,
        format_confidence="high",
        privacy_status=privacy,
        view_count=view_count,
        ingest_source="uploads",
        metadata_fetched_at=NOW - timedelta(hours=6),
    )


def metrics(
    record: VideoRecord,
    *,
    velocity: float,
    snapshots_basis: bool = True,
    outlier_ratio: float | None = None,
    comparison_age: int = 7,
    views_at_age: int | None = 20_000,
) -> VideoMetrics:
    age = (NOW - record.published_at).total_seconds() / 86_400
    recent = (
        Velocity(views_per_day=velocity, from_at=NOW - timedelta(days=7), to_at=NOW, window_hours=7 * 24)
        if snapshots_basis
        else None
    )
    outlier = None
    if outlier_ratio is not None:
        outlier = OutlierAssessment(
            age_days=comparison_age,
            method=AgeMethod.observed,
            value=views_at_age or 0,
            baseline_median=(views_at_age or 0) / outlier_ratio,
            baseline_n=20,
            baseline_id="bl_fixture",
            ratio=outlier_ratio,
            log_robust_z=2.5,
            tier=OutlierTier.outlier if outlier_ratio >= 3 else OutlierTier.normal,
            is_outlier=outlier_ratio >= 3,
        )
    segment = FormatSegment.short_form if record.format is VideoFormat.short_form else FormatSegment.long_form
    return VideoMetrics(
        video_id=record.video_id,
        channel_id=record.channel_id,
        as_of=NOW,
        segment=segment,
        format_key=f"{segment.value}|mid",
        age_anchor_at=record.published_at,
        age_days=age,
        views_now=record.view_count,
        likes_now=None,
        comments_now=None,
        views_at_age={
            comparison_age: ViewsAtAge(
                video_id=record.video_id,
                age_days=comparison_age,
                views=views_at_age,
                method=AgeMethod.observed,
                source="data_api_snapshot",
                label=f"views at day {comparison_age} (observed)",
                as_of=NOW,
                source_oldest_at=NOW - timedelta(days=20),
            )
        },
        comparison_age_days=comparison_age,
        views_per_day_lifetime=record.view_count / max(age, 1.0),
        velocity_recent=recent,
        velocity_7d=recent,
        engagement_basis="none",
        outlier=outlier,
        metrics_version="fixture",
    )


def topic_ref(label: str, kind: TopicKind = TopicKind.keyword, offering_ids: Sequence[str] = ()) -> TopicRef:
    return TopicRef(
        id=topic_id(label),
        client_id=CLIENT_ID,
        profile_version=1,
        kind=kind,
        source_item_id=label,
        label=label,
        offering_ids=list(offering_ids),
        fingerprint=label,
    )


def match(vid: str, ref: TopicRef, score: float = 0.85) -> TopicMatch:
    return TopicMatch(
        client_id=CLIENT_ID,
        video_id=vid,
        topic_ref_id=ref.id,
        topic_kind=ref.kind,
        profile_version=1,
        universe_hash="u",
        prepared_hash="p",
        engine_version="fixture",
        decision="accepted",
        lexical_score=score,
        combined_score=score,
        method=MatchMethod.lexical,
        confidence="high",
        explanation="fixture",
        matched_at=NOW,
    )


def news_signal(
    entity: str,
    *,
    topic_ref_ids: Sequence[str],
    kind: str = "model",
    event_days_ago: int = 2,
    relevance: float = 0.9,
    status: str = "active",
    sources: Sequence[NewsSourceRef] | None = None,
    signal_id: uuid.UUID | None = None,
    first_seen_days_ago: float | None = None,
) -> NewsSignal:
    event_day = (NOW - timedelta(days=event_days_ago)).date()
    first_seen = NOW - timedelta(
        days=first_seen_days_ago if first_seen_days_ago is not None else event_days_ago
    )
    return NewsSignal(
        id=signal_id or uuid.uuid5(uuid.NAMESPACE_URL, entity),
        client_id=CLIENT_ID,
        story_id=uuid.uuid5(uuid.NAMESPACE_URL, "story" + entity),
        profile_version=1,
        judgement=NewsJudgement(
            relevance_band=RelevanceBand.direct,
            key_entity=GroundedEntity(
                text=entity, canonical=entity, kind=kind, evidence_item_id=uuid.uuid4(), evidence_quote=entity
            ),
            event_type=EventType.model_release,
            event_date=event_day,
            confirmation=ConfirmationStatus.confirmed,
            angle="launch",
            region_relevance="global",
            about_client=False,
        ),
        credibility=CredibilityAssessment(
            best_tier=SourceTier.tier1_primary,
            independent_outlets=3,
            has_primary_source=True,
            has_authoritative_health_source=False,
            score=0.9,
        ),
        demand=DemandCheck(
            status="checked",
            entity_query=entity,
            published_after=NOW - timedelta(days=3),
            checked_at=NOW,
            early_video_count=4,
            channel_count=3,
            outlier_count=2,
            median_views_per_hour=50.0,
            saturation="emerging",
            quota_units_spent=1,
        ),
        is_competitor_entity=False,
        topic_ref_ids=list(topic_ref_ids),
        first_seen_at=first_seen,
        sources=list(
            sources
            or [
                NewsSourceRef(
                    url="https://www.anthropic.com/news/fable-5-2",
                    publisher="Anthropic",
                    published_at=NOW - timedelta(days=event_days_ago),
                    tier=SourceTier.tier1_primary,
                )
            ]
        ),
        verification_status=status,
        relevance_score=relevance,
        urgency="now",
        half_life_hours=72,
        expires_at=NOW + timedelta(days=10),
        refresh_eligible=True,
        safety=SafetyDecision(decision="allow"),
        status="new",
        created_at=first_seen,
    )


def reference(
    vid: str, title: str, channel_id: str, *, ratio: float = 4.0, days_ago: float = 3, description: str = ""
) -> ReferenceCandidate:
    record = video(
        vid,
        title=title,
        description=description,
        published_days_ago=days_ago,
        channel_id=channel_id,
        channel_title=f"Channel {channel_id[-1]}",
    )
    return ReferenceCandidate(
        video=record,
        metrics=metrics(record, velocity=500, outlier_ratio=ratio),
        role=ChannelRole.niche,
        matched_topic_ids=[],
    )


class FakeProbe:
    """deterministic link probe: syntax first, then a lookup of known statuses (ok by default)."""

    def __init__(self, statuses: dict[str, LinkStatusClass] | None = None) -> None:
        self.statuses = statuses or {}
        self.calls: list[str] = []

    async def probe(self, url: str, previous: LinkStatus | None = None) -> LinkStatus:
        self.calls.append(url)
        key = normalise_url(url)
        problem = malformation(url)
        if problem is not None:
            return LinkStatus(
                key, LinkStatusClass.malformed, NOW, error=problem.reason, repaired_url=problem.repaired
            )
        status = self.statuses.get(key, LinkStatusClass.ok)
        return LinkStatus(key, status, NOW, http_status=404 if status is LinkStatusClass.dead else 200)


def engine(
    settings: RefreshSettings | None = None, probe: FakeProbe | None = None, llm: Any = None
) -> RefreshEngine:
    settings = settings or RefreshSettings()
    health = LinkHealthService(settings, FrozenClock(NOW), probe or FakeProbe())
    return RefreshEngine(settings, health, llm)


def inputs(**fields: Any) -> RefreshInputs:
    return RefreshInputs(
        client_id=CLIENT_ID, run_id=RUN_ID, as_of=NOW, window_days=30, channel_verified=True, **fields
    )


def snapshots_linear(
    vid: str, *, start_views: int, per_day: Sequence[float], start: date
) -> list[VideoSnapshot]:
    # one snapshot per day; per_day[i] is the views gained on day i
    series = []
    views = float(start_views)
    for offset in range(len(per_day) + 1):
        at = datetime.combine(start + timedelta(days=offset), datetime.min.time(), tzinfo=UTC)
        series.append(
            VideoSnapshot(
                video_id=vid,
                slot_at=at,
                captured_at=at,
                age_hours=0.0,
                view_count=int(views),
                source="batch_stats",
                counting_regime="v2",
            )
        )
        if offset < len(per_day):
            views += per_day[offset]
    return series
