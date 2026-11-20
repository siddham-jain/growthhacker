from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.domain.analytics import AgeMethod
from app.domain.confidence import Confidence, ConfidenceBasis, ConfidenceLabel, ConfidenceRuleId
from app.domain.news import (
    ConfirmationStatus,
    CredibilityAssessment,
    DemandCheck,
    EarlyVideo,
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
    Evidence,
    FreshnessProfile,
    FreshnessSensitivity,
    OperatingLevel,
    ProfileQuality,
    RefreshPolicy,
)
from app.domain.recommendations import (
    CreatorConsistency,
    CreatorPerformance,
    CreatorRelevance,
    CreatorSubject,
    InsufficiencyCounts,
    InsufficiencyNotice,
    InsufficiencySuggestion,
    NewsCompliance,
    NewsExtras,
    NewsSubject,
    Recommendation,
    RecommendationDraft,
    RecommendationSet,
    RecType,
    make_stable_key,
    recommendation_id,
)
from app.domain.refresh import (
    ClaimLevel,
    DescriptionRefresh,
    InsertionAnchor,
    InsertionKind,
    InsertionOp,
    InsertionPosition,
    RefreshStatus,
)
from app.domain.runs import OutlierFoundPayload, RunEvent, RunEventType, StageStatus
from app.domain.web import ExtractionKind, SourceKind
from tests.contracts.factories import (
    CHANNEL_IDS,
    CLIENT_ID,
    GENERATED_AT,
    RUN_ID,
    VIDEO_IDS,
    make_extras,
    make_recommendation,
    make_reference_video,
    make_views_at_age,
)

STORY_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-bbbbbbbbbbbb")
SIGNAL_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-cccccccccccc")


def _meta(origin: ClaimOrigin = ClaimOrigin.extracted) -> ClaimMeta:
    evidence = Evidence(
        source_kind=SourceKind.site_direct,
        extraction=ExtractionKind.json_ld,
        source_url="https://www.apollohospitals.com/",
        quote="Apollo Hospitals",
        match_score=100.0,
        fetched_at=GENERATED_AT,
    )
    return ClaimMeta(
        origin=origin,
        status=ClaimStatus.proposed,
        confidence=0.8,
        evidence=[evidence] if origin is ClaimOrigin.extracted else [],
        basis_ids=[] if origin is ClaimOrigin.extracted else ["F0001"],
        first_seen_version=1,
        updated_at=GENERATED_AT,
    )


def build_business_profile() -> BusinessProfile:
    return BusinessProfile(
        profile_id=UUID("0190f2a4-6c1e-7d3a-9b2f-dddddddddddd"),
        client_id=CLIENT_ID,
        version=1,
        status="awaiting_confirmation",
        generated_at=GENERATED_AT,
        identity=BusinessIdentity(
            name="Apollo Hospitals",
            name_meta=_meta(),
            one_liner="Multispeciality hospital network across India",
            one_liner_meta=_meta(ClaimOrigin.derived),
            industry="healthcare_hospital",
            industry_meta=_meta(ClaimOrigin.derived),
            niche="multispeciality hospital network",
            business_model=BusinessModel.multi_location_service,
            geo_scope="national",
            primary_language="en",
            languages=["en", "hi"],
            country="IN",
            timezone="Asia/Kolkata",
            website=None,
        ),
        freshness=FreshnessProfile(
            sensitivity=FreshnessSensitivity.medium,
            sensitivity_score=0.55,
            score_components={"llm": 0.5, "industry_prior": 0.55, "site_signal": 0.6},
            rationale="Health guidelines and seasonal outbreaks shift what patients search for",
            regulated_domain=True,
            domain_kind="healthcare",
            refresh_policy=RefreshPolicy(
                news_scan_interval_hours=24,
                refresh_check_interval_days=30,
                description_refresh_eligible=True,
                min_signal_strength=0.6,
            ),
            meta=_meta(ClaimOrigin.inferred),
        ),
        quality=ProfileQuality(
            claims_total=120,
            claims_verified=110,
            claims_dropped_unverified=10,
            injection_flags=0,
            thin_site=False,
            label="rich",
            completeness=0.85,
        ),
    )


def build_news_signal() -> NewsSignal:
    entity = GroundedEntity(
        text="Nova Atlas 5.2",
        canonical="Nova Atlas 5.2",
        kind="model",
        evidence_item_id=STORY_ID,
        evidence_quote="Example Labs released Nova Atlas 5.2 today",
    )
    return NewsSignal(
        id=SIGNAL_ID,
        client_id=CLIENT_ID,
        story_id=STORY_ID,
        profile_version=1,
        judgement=NewsJudgement(
            relevance_band=RelevanceBand.direct,
            key_entity=entity,
            event_type=EventType.model_release,
            event_date=GENERATED_AT.date(),
            confirmation=ConfirmationStatus.confirmed,
            angle="Show what the new model changes for slide decks",
            region_relevance="global",
            about_client=False,
        ),
        credibility=CredibilityAssessment(
            best_tier=SourceTier.tier1_primary,
            independent_outlets=9,
            has_primary_source=True,
            has_authoritative_health_source=False,
            score=0.95,
        ),
        demand=DemandCheck(
            status="checked",
            entity_query="nova atlas 5.2",
            published_after=GENERATED_AT - timedelta(hours=12),
            checked_at=GENERATED_AT,
            early_videos=[
                EarlyVideo(
                    video_id=VIDEO_IDS[0],
                    channel_id=CHANNEL_IDS[1],
                    channel_title="Tech Explained",
                    published_at=GENERATED_AT - timedelta(hours=10),
                    age_hours=10.0,
                    views=12_000,
                    views_per_hour=1_200.0,
                    views_at_age=make_views_at_age(age_days=0, views=12_000),
                    outlier_ratio=3.4,
                    is_outlier=True,
                    duration_band="8-15m",
                )
            ],
            early_video_count=1,
            channel_count=1,
            outlier_count=1,
            median_views_per_hour=1_200.0,
            saturation="emerging",
            quota_units_spent=1,
        ),
        is_competitor_entity=False,
        first_seen_at=GENERATED_AT - timedelta(hours=14),
        sources=[
            NewsSourceRef(url="https://labs.example.com/news", publisher="Example Labs", published_at=None)
        ],
        verification_status="active",
        relevance_score=0.9,
        urgency="now",
        half_life_hours=72,
        expires_at=GENERATED_AT + timedelta(days=3),
        refresh_eligible=True,
        safety=SafetyDecision(decision="allow"),
        status="new",
        created_at=GENERATED_AT,
    )


def _insertion() -> InsertionOp:
    text = "Update (Nov 2026): Nova Atlas 5.2 is now available; the workflow shown here applies to it"
    return InsertionOp(
        op_id="op1",
        kind=InsertionKind.insert,
        position=InsertionPosition.top_line,
        anchor=InsertionAnchor(
            strategy="top", anchor_excerpt="How to make money using AI", anchor_hash="ab12"
        ),
        text=text,
        text_bytes=len(text.encode("utf-8")),
        keywords_added=["Nova Atlas 5.2"],
        claim_level=ClaimLevel.l3_applicability,
        phrased_by="template",
    )


def build_description_refresh() -> DescriptionRefresh:
    insertion = _insertion()
    return DescriptionRefresh(
        id="ref_1",
        client_id=CLIENT_ID,
        video_id=VIDEO_IDS[1],
        run_id=RUN_ID,
        triggers=[],
        operations=[insertion],
        inserted_bytes=insertion.text_bytes + 1,
        resulting_bytes=1_800,
        expected_effect="May help the video surface for searches on the new model name",
        confidence=Confidence(
            label=ConfidenceLabel.medium,
            outliers=3,
            channels=2,
            rule_id=ConfidenceRuleId.refresh_v1,
        ),
        priority=0.7,
        expires_at=GENERATED_AT + timedelta(days=14),
        status=RefreshStatus.proposed,
        description_hash_at_generation="d41d8cd9",
        review_required=False,
        generated_at=GENERATED_AT,
    )


def build_run_event() -> RunEvent:
    return RunEvent(
        run_id=RUN_ID,
        client_id=CLIENT_ID,
        seq=42,
        type=RunEventType.insight,
        stage="analytics",
        step="analytics.outliers",
        status=StageStatus.running,
        message="Found an outlier at 3.6x its channel's normal",
        ts=GENERATED_AT,
        payload=OutlierFoundPayload(
            video_id=VIDEO_IDS[0],
            channel_title="Fortis Healthcare",
            title="Knee replacement cost explained",
            outlier_ratio=3.6,
            views_at_age=make_views_at_age(age_days=7, views=18_400, method=AgeMethod.interpolated),
        ),
    )


def build_creator_draft() -> RecommendationDraft:
    return RecommendationDraft(
        draft_id="d_creator",
        stable_key=make_stable_key(CLIENT_ID, RecType.creator, f"creator:{CHANNEL_IDS[2]}", 30),
        client_id=CLIENT_ID,
        type=RecType.creator,
        window_days=30,
        as_of=GENERATED_AT,
        produced_by="creator@1",
        subject=CreatorSubject(
            channel_id=CHANNEL_IDS[2],
            title="Dr. Health Explainer",
            url=f"https://www.youtube.com/channel/{CHANNEL_IDS[2]}",
            subscriber_band="100k_1m",
            uploads_90d=24,
            uploads_checked=24,
            relevance=CreatorRelevance(match_share=0.4, topics_covered=5),
            performance=CreatorPerformance(
                median_outlier_ratio=1.8,
                median_views_at_age=22_000,
                niche_median_views_at_age=9_000,
                comparison_age_days=7,
            ),
            consistency=CreatorConsistency(
                median_gap_days=3.5,
                gap_cv=0.4,
                hit_rate=0.3,
                last_upload_at=GENERATED_AT.date(),
            ),
        ),
        references=[make_reference_video(i) for i in range(3)],
    )


def build_news_recommendation() -> Recommendation:
    source = NewsSourceRef(
        url="https://labs.example.com/news",
        publisher="Example Labs",
        published_at=GENERATED_AT - timedelta(hours=14),
        tier=SourceTier.tier1_primary,
        title="Introducing Nova Atlas 5.2",
    )
    compliance = NewsCompliance(review_required=False)
    subject = NewsSubject(
        news_signal_id=SIGNAL_ID,
        story_id=STORY_ID,
        key_entity="Nova Atlas 5.2",
        angle="What the new model changes for slide decks",
        urgency="now",
        expires_at=GENERATED_AT + timedelta(days=3),
        reference_sources=[source],
        compliance=compliance,
    )
    stable_key = make_stable_key(CLIENT_ID, RecType.news_opportunity, "news:nova-atlas-5.2", 7)
    return make_recommendation(
        type=RecType.news_opportunity,
        id=recommendation_id(stable_key),
        window_days=7,
        reference_videos=[
            make_reference_video(i, views_day7=None, views_at_age=make_views_at_age(age_days=1, views=12_000))
            for i in range(3)
        ],
        confidence=Confidence(label=ConfidenceLabel.medium, outliers=3, channels=2, rule_id="news_v1"),
        extras=make_extras(
            stable_key,
            subject=subject,
            news=NewsExtras(reference_sources=[source], urgency="now", compliance=compliance),
        ),
    )


def build_recommendation_set() -> RecommendationSet:
    return RecommendationSet(
        client_id=CLIENT_ID,
        window_days=7,
        as_of=GENERATED_AT,
        generated_at=GENERATED_AT,
        recommendations=[make_recommendation()],
        notices=[
            InsufficiencyNotice(
                kind="thin_window",
                window_days=7,
                found=InsufficiencyCounts(outliers=2, channels=1, cards=1),
                required=InsufficiencyCounts(outliers=5, channels=2, cards=3),
                suggestion=InsufficiencySuggestion(
                    action="widen_window",
                    widen_to=30,
                    projected=InsufficiencyCounts(outliers=14, channels=5, cards=6),
                ),
                reason_code="THIN_WINDOW",
            )
        ],
        suppressed_counts={"NoCopyingText": 1},
        candidate_count=11,
    )


BUILDERS: dict[str, Callable[[], BaseModel]] = {
    "recommendation": make_recommendation,
    "news_recommendation": build_news_recommendation,
    "strict_recommendation": lambda: make_recommendation().strict_contract(),
    "recommendation_set": build_recommendation_set,
    "creator_draft": build_creator_draft,
    "business_profile": build_business_profile,
    "news_signal": build_news_signal,
    "description_refresh": build_description_refresh,
    "run_event": build_run_event,
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_json_round_trip_is_lossless(name: str):
    original = BUILDERS[name]()
    restored = type(original).model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.model_dump_json() == original.model_dump_json()


def test_derived_accessors_survive_round_trip():
    signal = NewsSignal.model_validate_json(build_news_signal().model_dump_json())
    profile = BusinessProfile.model_validate_json(build_business_profile().model_dump_json())

    assert signal.entity == "Nova Atlas 5.2"
    assert signal.demand_video_ids == [VIDEO_IDS[0]]
    assert profile.freshness.operating_level is OperatingLevel.medium


def test_first_mover_basis_round_trips_as_low_confidence():
    confidence = Confidence(
        label=ConfidenceLabel.low, outliers=0, channels=0, basis=ConfidenceBasis.first_mover
    )

    assert Confidence.model_validate_json(confidence.model_dump_json()) == confidence
