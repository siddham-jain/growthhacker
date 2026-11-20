"""apollo-like healthcare niche: 5 competitors, niche creators, own channel; fixtures of 02/03/04/05/06/08/09 outputs."""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.analytics import (
    AgeMethod,
    ChannelBaseline,
    ConsistencyStats,
    ConversionLens,
    DescriptionTraits,
    GapCell,
    GapMatrix,
    LiftScope,
    OutlierAssessment,
    OutlierTier,
    OwnPerformanceReport,
    SegmentPerf,
    ThumbnailTraits,
    TimingTraits,
    TitleTraits,
    TraitLift,
    VideoMetrics,
    VideoPerf,
    VideoTraits,
    ViewsAtAge,
    ViewsAtAgeSummary,
)
from app.domain.common import ChannelRole, DirectnessLevel, FormatSegment, VideoFormat
from app.domain.competitors import (
    BusinessRelevance,
    CandidateRole,
    CandidateSource,
    CandidateStatus,
    ChannelEvidence,
    ChannelMatch,
    CompetitorCandidate,
    CompetitorRelation,
    CompetitorSet,
    OwnerKind,
    YouTubeRelevance,
)
from app.domain.confidence import Confidence, ConfidenceBasis, ConfidenceLabel, ConfidenceRuleId
from app.domain.news import NewsSourceRef
from app.domain.profile import (
    BuyerQuestion,
    BuyerQuestionIntent,
    BuyerQuestionSourceKind,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    FunnelStage,
    ReviewStatus,
)
from app.domain.recommendations import (
    EvidenceSource,
    NewsCompliance,
    NewsSubject,
    Reason,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    RefreshSubject,
    make_stable_key,
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
from app.domain.topics import (
    AnswerDirectness,
    DirectnessComponents,
    MatchMethod,
    TopicKind,
    TopicMatch,
    TopicRef,
)
from app.domain.youtube import ChannelRecord, OwnershipLabel, OwnershipVerdict, VideoRecord
from app.services.recommend.inputs import ClientFacts, RecommendInputs, WindowAnalytics

AS_OF = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
GENERATED_AT = datetime(2026, 11, 20, 9, 5, tzinfo=UTC)
CLIENT_ID = uuid.UUID("0190f2a4-6c1e-7d3a-9b2f-1a2b3c4d5e6f")
RUN_ID = uuid.UUID("0190f2a4-6c1e-7d3a-9b2f-aaaaaaaaaaaa")
COMPARISON_AGE = {7: 3, 30: 7, 90: 7}


def channel_id(key: str) -> str:
    return "UC" + (key + "0" * 22)[:22]


def video_id(key: str) -> str:
    return ("vid" + key + "_" * 11)[:11]


def topic_id(key: str) -> str:
    return "tr_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def question_id(key: str) -> uuid.UUID:
    return uuid.uuid5(CLIENT_ID, key)


def meta() -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.user,
        status=ClaimStatus.confirmed,
        confidence=0.9,
        first_seen_version=1,
        updated_at=AS_OF,
    )


@dataclass(frozen=True)
class ChannelSpec:
    key: str
    title: str
    handle: str
    role: ChannelRole
    baseline: float
    subscribers: int | None = 50_000
    description: str = ""
    made_for_kids: bool = False


@dataclass(frozen=True)
class TopicSpec:
    key: str
    kind: TopicKind
    label: str
    stage: FunnelStage | None = None
    intent: BuyerQuestionIntent = BuyerQuestionIntent.other


@dataclass(frozen=True)
class VideoSpec:
    key: str
    channel: str
    title: str
    days_ago: float
    ratio: float | None
    segment: str = "long_form"
    band: str = "3–8m"
    topics: dict[str, DirectnessLevel | None] = field(default_factory=dict)
    question: bool = False
    number: bool = False
    how_to: bool = False
    year: bool = False
    location: bool = False
    thumb_text: bool = True
    paid: bool | None = False
    description: str = ""
    method: AgeMethod = AgeMethod.observed

    @property
    def is_outlier(self) -> bool:
        return self.ratio is not None and self.ratio >= 3.0


CHANNELS = [
    ChannelSpec("apollo", "Apollo Hospitals", "@ApolloHospitals", ChannelRole.own, 1_320, 900_000),
    ChannelSpec("fortis", "Fortis Healthcare", "@FortisHealthcare", ChannelRole.competitor, 4_000, 400_000),
    ChannelSpec("narayana", "Narayana Health", "@NarayanaHealth", ChannelRole.competitor, 3_000, 300_000),
    ChannelSpec("medanta", "Medanta", "@Medanta", ChannelRole.competitor, 5_000, 600_000),
    ChannelSpec("maxhc", "Max Healthcare", "@MaxHealthcare", ChannelRole.competitor, 3_500, 350_000),
    ChannelSpec("manipal", "Manipal Hospitals", "@ManipalHospitals", ChannelRole.competitor, 2_500, 250_000),
    ChannelSpec(
        "drheart", "Dr Heart Talks", "@DrHeartTalks", ChannelRole.niche, 1_500, 85_000, "Cardiology explained"
    ),
    ChannelSpec(
        "ortho", "Ortho Simplified", "@OrthoSimplified", ChannelRole.niche, 900, 40_000, "Joints and spines"
    ),
    ChannelSpec("healthdesk", "Health Desk India", "@HealthDeskIndia", ChannelRole.niche, 2_000, 120_000),
    ChannelSpec(
        "kidsfun", "Kids Health Fun", "@KidsHealthFun", ChannelRole.niche, 800, 20_000, made_for_kids=True
    ),
    ChannelSpec(
        "practo", "Practo", "@Practo", ChannelRole.niche, 3_000, 500_000, "Book doctors at practo.com"
    ),
]
CHANNEL_BY_KEY = {spec.key: spec for spec in CHANNELS}
COMPETITOR_KEYS = ("fortis", "narayana", "medanta", "maxhc", "manipal")

TOPICS = [
    TopicSpec(
        "bq_knee",
        TopicKind.buyer_question,
        "How much does a knee replacement cost at Apollo?",
        FunnelStage.decision,
        BuyerQuestionIntent.cost,
    ),
    TopicSpec(
        "bq_angio",
        TopicKind.buyer_question,
        "Which hospital is best for angioplasty in Chennai?",
        FunnelStage.decision,
        BuyerQuestionIntent.comparison,
    ),
    TopicSpec(
        "bq_heart",
        TopicKind.buyer_question,
        "What are the early signs of a heart attack?",
        FunnelStage.awareness,
    ),
    TopicSpec(
        "bq_kidney",
        TopicKind.buyer_question,
        "Does Apollo offer cashless insurance for kidney transplant?",
        FunnelStage.decision,
        BuyerQuestionIntent.cost,
    ),
    TopicSpec("kw_knee", TopicKind.keyword, "knee replacement"),
    TopicSpec("kw_angio", TopicKind.keyword, "angioplasty"),
    TopicSpec("kw_health", TopicKind.keyword, "health"),
    TopicSpec("kw_spine", TopicKind.keyword, "spine surgery"),
]

D, P = DirectnessLevel.direct, DirectnessLevel.partial
H = {"kw_health": None}

VIDEOS = [
    VideoSpec(
        "k1",
        "fortis",
        "What does a knee replacement really cost?",
        6,
        4.2,
        topics={"bq_knee": D, "kw_knee": None, **H},
        question=True,
    ),
    VideoSpec(
        "k2",
        "narayana",
        "Knee replacement price breakdown for 2026",
        9,
        3.6,
        topics={"bq_knee": D, "kw_knee": None, **H},
        number=True,
        year=True,
    ),
    VideoSpec(
        "k3",
        "medanta",
        "Is a knee replacement worth the cost?",
        12,
        5.1,
        topics={"bq_knee": D, "kw_knee": None, **H},
        question=True,
    ),
    VideoSpec(
        "k4",
        "ortho",
        "Knee surgery costs: what patients pay",
        15,
        3.3,
        topics={"bq_knee": D, "kw_knee": None, **H},
    ),
    VideoSpec(
        "k5",
        "maxhc",
        "How much is total knee replacement in Delhi?",
        18,
        3.9,
        topics={"bq_knee": D, "kw_knee": None, **H},
        question=True,
        location=True,
    ),
    VideoSpec(
        "k6",
        "manipal",
        "Knee replacement recovery timeline",
        20,
        1.4,
        band="8–15m",
        topics={"kw_knee": None, **H},
    ),
    VideoSpec(
        "k7",
        "ortho",
        "Knee pain exercises at home",
        22,
        0.8,
        segment="short_form",
        band="≤60s",
        topics={"kw_knee": None, **H},
        thumb_text=False,
    ),
    VideoSpec(
        "k8",
        "drheart",
        "Robotic knee replacement cost explained",
        8,
        3.1,
        topics={"bq_knee": D, "kw_knee": None, **H},
    ),
    VideoSpec(
        "a1",
        "narayana",
        "Angioplasty vs bypass: which is right for you?",
        7,
        3.4,
        topics={"bq_angio": D, "kw_angio": None, **H},
        question=True,
    ),
    VideoSpec(
        "a2",
        "drheart",
        "Choosing a hospital for angioplasty in Chennai",
        10,
        3.8,
        topics={"bq_angio": D, "kw_angio": None, **H},
        location=True,
        description="Today's episode.\nSponsored by @FortisHealthcare - book a heart check.",
    ),
    VideoSpec(
        "a3",
        "fortis",
        "What happens during angioplasty",
        14,
        2.2,
        band="8–15m",
        topics={"kw_angio": None, **H},
    ),
    VideoSpec(
        "a4",
        "drheart",
        "Angioplasty recovery: the first week",
        25,
        3.2,
        topics={"bq_angio": D, "kw_angio": None, **H},
    ),
    VideoSpec(
        "a5",
        "medanta",
        "Stent or no stent? Angioplasty questions answered",
        27,
        1.1,
        topics={"kw_angio": None, **H},
        question=True,
    ),
    VideoSpec(
        "h1",
        "healthdesk",
        "5 early signs of a heart attack",
        4,
        4.5,
        segment="short_form",
        band="≤60s",
        topics={"bq_heart": D, **H},
        number=True,
    ),
    VideoSpec(
        "h2",
        "drheart",
        "Heart attack warning signs women ignore",
        11,
        3.7,
        segment="short_form",
        band="≤60s",
        topics={"bq_heart": D, **H},
    ),
    VideoSpec(
        "h3",
        "maxhc",
        "Silent heart attack symptoms",
        16,
        3.2,
        segment="short_form",
        band="61–180s",
        topics={"bq_heart": D, **H},
    ),
    VideoSpec(
        "h4",
        "fortis",
        "Chest pain: when should you go to the ER?",
        19,
        1.2,
        topics={"bq_heart": P, **H},
        question=True,
    ),
    VideoSpec("s1", "manipal", "Spine surgery myths", 6, 1.3, topics={"kw_spine": None, **H}),
    VideoSpec("s2", "medanta", "Minimally invasive spine surgery", 13, 0.9, topics={"kw_spine": None, **H}),
    VideoSpec("s3", "ortho", "Slip disc treatment options", 21, 1.0, topics={"kw_spine": None, **H}),
    VideoSpec(
        "n1",
        "healthdesk",
        "Robotic knee implant gets approval in India",
        2.5,
        3.4,
        topics={"kw_knee": None, **H},
    ),
    VideoSpec(
        "n2", "ortho", "New knee implant: what changes for patients", 2.5, 3.1, topics={"kw_knee": None, **H}
    ),
    VideoSpec(
        "n3", "maxhc", "Robot-assisted knee surgery now available", 3, 3.3, topics={"kw_knee": None, **H}
    ),
    VideoSpec(
        "g1", "healthdesk", "Monsoon infections to watch", 5, 3.5, segment="short_form", band="≤60s", topics=H
    ),
    VideoSpec("g2", "fortis", "Meet our nursing team", 9, 0.6, topics={}),
    VideoSpec("g3", "narayana", "Hospital tour", 12, 0.7, topics={}),
    VideoSpec("g4", "medanta", "Doctor's day special", 17, 0.9, topics={}),
    VideoSpec("g5", "manipal", "Patient story: Ramesh", 23, 1.1, topics={}),
    VideoSpec("g6", "maxhc", "Healthy breakfast ideas", 26, 0.8, topics=H),
    VideoSpec("g7", "kidsfun", "Brush your teeth song", 6, 4.0, segment="short_form", band="≤60s", topics=H),
    VideoSpec("g8", "practo", "Book a knee consultation online", 10, 3.2, topics={"kw_knee": None, **H}),
    VideoSpec("g9", "practo", "Knee replacement cost calculator", 14, 3.0, topics={"kw_knee": None, **H}),
    VideoSpec(
        "g10",
        "kidsfun",
        "Knee scrapes for kids",
        12,
        1.0,
        segment="short_form",
        band="≤60s",
        topics={"kw_knee": None},
    ),
    VideoSpec("d5", "drheart", "Heart health basics", 40, 1.0, topics=H),
    VideoSpec(
        "d6",
        "drheart",
        "Angioplasty myths busted",
        50,
        2.0,
        topics={"kw_angio": None, **H},
        paid=True,
        description="Thanks to fortishealthcare.com for supporting this angioplasty explainer.",
    ),
    VideoSpec("d7", "drheart", "Cholesterol and your heart", 60, 0.9, topics={}),
    VideoSpec("d8", "drheart", "Bypass surgery explained", 75, 1.5, topics=H),
    VideoSpec("o9", "ortho", "Shoulder pain basics", 60, 1.0, topics=H),
    VideoSpec(
        "o1",
        "apollo",
        "Heart attack: know the signs",
        3,
        None,
        topics={"bq_heart": D, **H},
        method=AgeMethod.pending,
    ),
    VideoSpec("o2", "apollo", "Knee pain causes and care", 40, 1.6, topics={"kw_knee": None, **H}),
    VideoSpec("o3", "apollo", "Hip replacement explained", 60, 1.3, topics=H),
    VideoSpec("o4", "apollo", "Kidney stones treatment", 90, 1.5, topics=H),
    VideoSpec("o5", "apollo", "Liver transplant journey", 120, 0.9, band="8–15m", topics=H),
    VideoSpec("o6", "apollo", "Diabetes diet tips", 150, 1.1, segment="short_form", band="≤60s", topics=H),
    VideoSpec(
        "o7",
        "apollo",
        "Angioplasty explained by our cardiologist",
        200,
        0.7,
        band="8–15m",
        topics={"kw_angio": None, **H},
    ),
    VideoSpec("o8", "apollo", "Thyroid myths", 250, 1.0, segment="short_form", band="≤60s", topics=H),
]


def _published(spec: VideoSpec) -> datetime:
    return AS_OF - timedelta(days=spec.days_ago)


def channel_record(spec: ChannelSpec) -> ChannelRecord:
    uploads = [video for video in VIDEOS if video.channel == spec.key]
    last = max((_published(video) for video in uploads), default=None)
    return ChannelRecord(
        channel_id=channel_id(spec.key),
        title=spec.title,
        handle=spec.handle,
        description=spec.description,
        country="IN",
        default_language="en",
        published_at=AS_OF - timedelta(days=3000),
        uploads_playlist_id="UU" + channel_id(spec.key)[2:],
        subscriber_count=spec.subscribers,
        hidden_subscriber_count=spec.subscribers is None,
        view_count=10_000_000,
        video_count=500,
        thumbnail_url=None,
        made_for_kids=spec.made_for_kids,
        uploads_90d=len(uploads),
        last_upload_at=last,
        metadata_fetched_at=AS_OF,
        stats_fetched_at=AS_OF,
    )


def video_record(spec: VideoSpec) -> VideoRecord:
    channel = CHANNEL_BY_KEY[spec.channel]
    published = _published(spec)
    duration = 45 if spec.segment == "short_form" else (600 if spec.band == "8–15m" else 360)
    return VideoRecord(
        video_id=video_id(spec.key),
        channel_id=channel_id(spec.channel),
        channel_title=channel.title,
        published_at=published,
        effective_published_at=published,
        title=spec.title,
        description=spec.description,
        content_hash=hashlib.sha1(spec.title.encode()).hexdigest(),
        description_hash=hashlib.sha1(spec.description.encode()).hexdigest(),
        duration_s=duration,
        format=VideoFormat(spec.segment),
        format_confidence="high",
        has_paid_product_placement=spec.paid,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id(spec.key)}/hqdefault.jpg",
        view_count=round((spec.ratio or 1.0) * channel.baseline * 1.5),
        ingest_source="uploads",
        metadata_fetched_at=AS_OF,
    )


def _tier(ratio: float | None) -> OutlierTier:
    if ratio is None:
        return OutlierTier.insufficient
    if ratio >= 5:
        return OutlierTier.strong_outlier
    if ratio >= 3:
        return OutlierTier.outlier
    if ratio >= 2:
        return OutlierTier.breakout
    if ratio >= 0.7:
        return OutlierTier.normal
    return OutlierTier.below


def video_metrics(spec: VideoSpec, window_age: int) -> VideoMetrics:
    # a video younger than the window's comparison age is compared at its own age (06 §5.3)
    comparison_age = min(window_age, int(spec.days_ago))
    channel = CHANNEL_BY_KEY[spec.channel]
    published = _published(spec)
    views = round((spec.ratio or 0) * channel.baseline) if spec.ratio is not None else None
    age_value = ViewsAtAge(
        video_id=video_id(spec.key),
        age_days=comparison_age,
        views=views,
        method=spec.method,
        source="data_api_snapshot",
        label=f"views at day {comparison_age} ({spec.method.value})",
        as_of=AS_OF,
        source_oldest_at=published,
    )
    outlier = OutlierAssessment(
        age_days=comparison_age,
        method=spec.method,
        value=views or 0,
        baseline_median=channel.baseline,
        baseline_n=20,
        baseline_id=f"bl_{spec.channel}",
        ratio=spec.ratio or 0.0,
        log_robust_z=1.0,
        tier=_tier(spec.ratio),
        is_outlier=spec.is_outlier,
    )
    return VideoMetrics(
        video_id=video_id(spec.key),
        channel_id=channel_id(spec.channel),
        as_of=AS_OF,
        segment=FormatSegment(spec.segment),
        format_key=f"{spec.segment}|{spec.band}",
        age_anchor_at=published,
        age_days=spec.days_ago,
        views_now=(views or 0) * 2,
        likes_now=100,
        comments_now=10,
        views_at_age={comparison_age: age_value},
        comparison_age_days=comparison_age,
        views_per_day_lifetime=100.0,
        engagement_basis="likes+comments",
        outlier=outlier,
        metrics_version="fixture-1",
    )


def video_traits(spec: VideoSpec) -> VideoTraits:
    published = _published(spec)
    title = TitleTraits(
        char_len=len(spec.title),
        char_len_band="31–50",
        word_count=len(spec.title.split()),
        is_question=spec.question,
        has_number=spec.number,
        has_listicle_number=spec.number and spec.title[0].isdigit(),
        is_how_to=spec.how_to,
        has_year=spec.year,
        year_is_current_or_next=spec.year,
        has_brackets=False,
        has_parentheses=False,
        is_first_person=False,
        has_superlative=False,
        caps_ratio=0.1,
        caps_band="<0.3",
        all_caps_word_count=0,
        has_emoji=False,
        emoji_count=0,
        separator="colon" if ":" in spec.title else "none",
        has_price_cost="cost" in spec.title.lower() or "price" in spec.title.lower(),
        has_buyer_question_phrasing=spec.question,
        mentions_doctor=False,
        script="latin",
        has_client_location=spec.location,
        has_niche_location=spec.location,
    )
    description = DescriptionTraits(
        char_len=len(spec.description),
        links_count=0,
        link_domains=[],
        has_external_link=False,
        has_own_site_link=False,
        has_shortener_link=False,
        has_booking_cta=False,
        has_phone=False,
        has_whatsapp=False,
        has_chapters=False,
        chapter_count=0,
        chapter_titles=[],
        hashtag_count=0,
        hashtags_ignored=False,
        has_paid_disclosure_text=False,
        mentioned_handles=[],
        collab_markers=[],
        has_client_site_link=False,
        first_two_lines_have_client_keyword=False,
    )
    thumbnail = ThumbnailTraits(
        video_id=video_id(spec.key),
        source_key="high",
        source_w=480,
        source_h=360,
        content_sha256="0" * 64,
        letterbox_cropped=False,
        pillarbox_detected=False,
        text_present=spec.thumb_text,
        word_count=3 if spec.thumb_text else 0,
        word_count_band="1–3" if spec.thumb_text else "0",
        text_box_count=1 if spec.thumb_text else 0,
        text_area_ratio=0.2 if spec.thumb_text else 0.0,
        largest_text_height_ratio=0.1 if spec.thumb_text else 0.0,
        brand_text_only=False,
        script_detected="latin" if spec.thumb_text else "none",
        ocr_engine="rapidocr",
        ocr_model="fixture",
        ocr_confidence_mean=0.9,
        brightness=0.5,
        brightness_band="mid",
        rms_contrast=0.3,
        contrast_band="high",
        colorfulness=40.0,
        colorfulness_band="medium",
        dominant_colour_count=3,
        traits_version="fixture-1",
        computed_at=AS_OF,
    )
    return VideoTraits(
        video_id=video_id(spec.key),
        traits_version="fixture-1",
        segment=FormatSegment(spec.segment),
        length_band=spec.band,
        format_key=f"{spec.segment}|{spec.band}",
        title=title,
        description=description,
        thumbnail=thumbnail,
        timing=TimingTraits(
            client_timezone="Asia/Kolkata",
            publish_weekday=published.weekday(),
            publish_hour_local=10,
            daypart="morning",
            is_weekend=published.weekday() >= 5,
        ),
    )


def topic_ref(spec: TopicSpec) -> TopicRef:
    source = str(question_id(spec.key)) if spec.kind is TopicKind.buyer_question else spec.key
    return TopicRef(
        id=topic_id(spec.key),
        client_id=CLIENT_ID,
        profile_version=1,
        kind=spec.kind,
        source_item_id=source,
        label=spec.label,
        funnel_stage=spec.stage,
        fingerprint=hashlib.sha1(spec.label.encode()).hexdigest(),
    )


def buyer_question(spec: TopicSpec, cited: list[str] | None = None) -> BuyerQuestion:
    return BuyerQuestion(
        id=question_id(spec.key),
        client_id=CLIENT_ID,
        text=spec.label,
        normalized_text=spec.label.lower(),
        language="en",
        funnel_stage=spec.stage or FunnelStage.awareness,
        intent=spec.intent,
        source=BuyerQuestionSourceKind.website_faq,
        status=ReviewStatus.confirmed,
        priority=1,
        ai_cited_video_ids=cited,
        meta=meta(),
    )


def topic_matches(videos: list[VideoSpec]) -> list[TopicMatch]:
    matches = []
    for spec in videos:
        for key in spec.topics:
            matches.append(
                TopicMatch(
                    client_id=CLIENT_ID,
                    video_id=video_id(spec.key),
                    topic_ref_id=topic_id(key),
                    topic_kind=next(topic.kind for topic in TOPICS if topic.key == key),
                    profile_version=1,
                    universe_hash="u1",
                    prepared_hash="p1",
                    engine_version="fixture",
                    decision="accepted",
                    lexical_score=0.9,
                    combined_score=0.85,
                    method=MatchMethod.lexical,
                    confidence="high",
                    explanation="fixture match",
                    matched_at=AS_OF,
                )
            )
    return matches


def directness(videos: list[VideoSpec]) -> list[AnswerDirectness]:
    items = []
    for spec in videos:
        for key, level in spec.topics.items():
            if level is None:
                continue
            items.append(
                AnswerDirectness(
                    client_id=CLIENT_ID,
                    video_id=video_id(spec.key),
                    buyer_question_ref_id=topic_id(key),
                    universe_hash="u1",
                    prepared_hash="p1",
                    score=0.85 if level is DirectnessLevel.direct else 0.5,
                    level=level,
                    components=DirectnessComponents(
                        question_alignment=0.8,
                        facet_coverage=0.8,
                        entity_coverage=0.8,
                        specificity=0.7,
                        intent_cues=0.6,
                        penalties=0.0,
                    ),
                    method="deterministic",
                    explanation="fixture",
                )
            )
    return items


def trait_lift(
    scope: LiftScope,
    key: str,
    value: str,
    window: int,
    *,
    n_with: int,
    n_without: int,
    outliers_with: int,
    outliers_without: int,
    ci: tuple[float, float],
    refs: list[str],
    status: str = "supported",
) -> TraitLift:
    rate_with = outliers_with / n_with
    rate_without = outliers_without / n_without
    return TraitLift(
        scope=scope,
        window_days=window,
        as_of=AS_OF,
        comparison_age_days=COMPARISON_AGE[window],
        comparison_method=AgeMethod.observed,
        trait_key=key,
        trait_value=value,
        n_with=n_with,
        n_without=n_without,
        outliers_with=outliers_with,
        outliers_without=outliers_without,
        outlier_rate_with=rate_with,
        outlier_rate_without=rate_without,
        rate_with_wilson=(max(0.0, rate_with - 0.2), min(1.0, rate_with + 0.2)),
        lift=round(rate_with / rate_without, 2),
        lift_smoothed=round(rate_with / rate_without, 2),
        lift_ci=ci,
        channel_balanced_lift=1.5,
        p_value=0.01,
        q_value=0.05,
        outlier_channels_with=3,
        max_channel_share=0.4,
        ratio_quantiles_with=(1.0, 2.0, 3.5),
        reference_video_ids=[video_id(key_) for key_ in refs],
        status=status,
    )


def lifts(window: int) -> list[TraitLift]:
    if window == 7:
        return []
    return [
        trait_lift(
            LiftScope.competitor_set,
            "title.is_question",
            "true",
            window,
            n_with=8,
            n_without=30,
            outliers_with=4,
            outliers_without=5,
            ci=(1.3, 4.0),
            refs=["k3", "k1", "k5", "a1"],
        ),
        trait_lift(
            LiftScope.niche,
            "thumbnail.text_present",
            "true",
            window,
            n_with=30,
            n_without=6,
            outliers_with=18,
            outliers_without=1,
            ci=(1.2, 9.0),
            refs=["k3", "h1", "k1"],
        ),
        trait_lift(
            LiftScope.niche,
            "format_key",
            "long_form|3–8m",
            window,
            n_with=24,
            n_without=14,
            outliers_with=13,
            outliers_without=6,
            ci=(1.1, 2.4),
            refs=["k3", "k1", "k5"],
        ),
        trait_lift(
            LiftScope.niche,
            "segment",
            "short_form",
            window,
            n_with=6,
            n_without=32,
            outliers_with=4,
            outliers_without=15,
            ci=(1.05, 3.2),
            refs=["h1", "h2", "h3"],
        ),
        trait_lift(
            LiftScope.niche,
            "title.has_year",
            "true",
            window,
            n_with=2,
            n_without=36,
            outliers_with=1,
            outliers_without=18,
            ci=(0.3, 3.0),
            refs=["k2"],
            status="exploratory",
        ),
    ]


def gap_matrix(window: int, videos: list[VideoSpec]) -> GapMatrix:
    cells: dict[str, dict[str, GapCell]] = {}
    for spec in videos:
        if spec.days_ago > window or CHANNEL_BY_KEY[spec.channel].role is ChannelRole.own:
            continue
        for key in spec.topics:
            row = cells.setdefault(topic_id(key), {})
            cell = row.get(
                channel_id(spec.channel),
                GapCell(n_videos=0, n_outliers=0, best_ratio=None, latest_published_at=None),
            )
            row[channel_id(spec.channel)] = GapCell(
                n_videos=cell.n_videos + 1,
                n_outliers=cell.n_outliers + spec.is_outlier,
                best_ratio=max(cell.best_ratio or 0.0, spec.ratio or 0.0),
                latest_published_at=max(cell.latest_published_at or _published(spec), _published(spec)),
            )
    return GapMatrix(window_days=window, as_of=AS_OF, cells=cells)


def own_report(window: int, videos: list[VideoSpec]) -> OwnPerformanceReport:
    own = [spec for spec in videos if spec.channel == "apollo"]
    age = COMPARISON_AGE[window]

    def baseline(segment: FormatSegment, median: float, n: int) -> ChannelBaseline:
        return ChannelBaseline(
            baseline_id=f"bl_apollo_{segment.value}",
            channel_id=channel_id("apollo"),
            segment=segment,
            metric="views_at_age",
            age_days=age,
            method=AgeMethod.observed,
            as_of=AS_OF,
            median=median,
            mean=median * 1.4,
            trimmed_mean_10=median * 1.1,
            p25=median * 0.6,
            p75=median * 1.5,
            mad_log10=0.2,
            n=n,
            video_ids=[],
            fallback_level="trailing",
            sufficient=True,
        )

    return OwnPerformanceReport(
        client_id=str(CLIENT_ID),
        channel_id=channel_id("apollo"),
        window_days=window,
        as_of=AS_OF,
        comparison_age_days=age,
        method_mix={AgeMethod.observed: len(own)},
        videos=[
            VideoPerf(
                video_id=video_id(spec.key),
                published_at=_published(spec),
                segment=FormatSegment(spec.segment),
                format_key=f"{spec.segment}|{spec.band}",
                ratio=spec.ratio,
                tier=_tier(spec.ratio),
                method=spec.method,
                topic_ref_ids=[topic_id(key) for key in spec.topics],
            )
            for spec in own
        ],
        median_ratio_window=1.1,
        median_ratio_prev_window=1.0,
        best=[],
        worst=[],
        by_format={"long_form|3–8m": SegmentPerf(n=3, median_ratio=1.5, iqr=(1.3, 1.6))},
        by_topic={},
        by_trait={},
        consistency=ConsistencyStats(
            uploads_per_week=0.3, gap_cv=0.6, hit_rate_at_or_above_baseline=0.6, log_ratio_iqr=0.3
        ),
        baselines=[
            baseline(FormatSegment.long_form, 1_320, 30),
            baseline(FormatSegment.short_form, 2_100, 12),
        ],
        conversion=ConversionLens(answer_directness={}, comment_intent={}, description_cta_coverage={}),
    )


def competitor_candidate(
    key: str,
    *,
    owner_kind: OwnerKind = OwnerKind.organisation,
    role=CandidateRole.competitor_set,
    status=CandidateStatus.confirmed,
    domain: str | None = None,
    aliases: list[str] | None = None,
) -> CompetitorCandidate:
    spec = CHANNEL_BY_KEY[key]
    return CompetitorCandidate(
        id=uuid.uuid5(CLIENT_ID, f"candidate-{key}"),
        client_id=CLIENT_ID,
        brand_name=spec.title,
        normalized_name=spec.title.lower(),
        aliases=aliases or [],
        official_domain=domain,
        relation=CompetitorRelation.direct,
        owner_kind=owner_kind,
        role=role,
        sources=[CandidateSource(kind="web_search", ref="fixture", retrieved_at=AS_OF)],
        business=BusinessRelevance(
            offering_overlap=0.8, geo_overlap=0.8, geo_basis="IN", source_agreement=0.8, tier="high"
        ),
        channels=[
            ChannelMatch(
                channel_id=channel_id(key),
                handle=spec.handle,
                title=spec.title,
                resolution_method="site_link",
                variant="main",
                is_primary=True,
                ownership=OwnershipVerdict(
                    label=OwnershipLabel.verified, method="link", score=0.95, signals=[]
                ),
                youtube=ChannelEvidence(
                    subscriber_count=spec.subscribers,
                    video_count=500,
                    country="IN",
                    published_at=AS_OF - timedelta(days=3000),
                    uploads_90d=10,
                    last_upload_at=AS_OF - timedelta(days=3),
                    fetched_at=AS_OF,
                ),
                youtube_relevance=YouTubeRelevance(
                    activity=0.8, content_overlap=0.6, size_band="peer", subscriber_band="100k_1m"
                ),
            )
        ],
        confidence="high",
        status=status,
        evidence_summary="fixture",
        first_seen_run_id=RUN_ID,
        last_seen_run_id=RUN_ID,
    )


def reference(spec: VideoSpec, window_age: int = 7) -> ReferenceVideo:
    comparison_age = min(window_age, int(spec.days_ago))
    channel = CHANNEL_BY_KEY[spec.channel]
    views = round((spec.ratio or 0) * channel.baseline)
    return ReferenceVideo(
        video_id=video_id(spec.key),
        url=f"https://www.youtube.com/watch?v={video_id(spec.key)}",
        title=spec.title,
        channel=channel.title,
        channel_id=channel_id(spec.channel),
        channel_role=channel.role,
        published_at=_published(spec),
        views_day7=views if comparison_age == 7 else None,
        views_at_age=ViewsAtAgeSummary(
            age_days=comparison_age,
            views=views,
            method=AgeMethod.observed,
            label=f"views at day {comparison_age}",
        ),
        outlier_ratio=spec.ratio,
        channel_baseline_views_at_age=channel.baseline,
        is_outlier=spec.is_outlier,
        format_key=f"{spec.segment}|{spec.band}",
        length_band=spec.band,
        matched_topic_ids=sorted(topic_id(key) for key in spec.topics),
    )


SPECS = {spec.key: spec for spec in VIDEOS}


def news_drafts(window: int) -> list[RecommendationDraft]:
    signal = uuid.uuid5(CLIENT_ID, "news-robotic-knee")
    subject = NewsSubject(
        news_signal_id=signal,
        story_id=uuid.uuid5(CLIENT_ID, "story-robotic-knee"),
        key_entity="Robotic knee implant",
        angle="Explain what the newly approved robotic knee implant means for patients choosing surgery",
        urgency="this_week",
        expires_at=AS_OF + timedelta(days=7),
        reference_sources=[
            NewsSourceRef(
                url="https://example.org/a", publisher="The Hindu", published_at=AS_OF - timedelta(days=3)
            ),
            NewsSourceRef(
                url="https://example.org/b", publisher="Mint", published_at=AS_OF - timedelta(days=3)
            ),
        ],
        compliance=NewsCompliance(review_required=True),
    )
    refs = [reference(SPECS[key]) for key in ("n1", "n3", "n2")]
    demand = Reason(
        reason_id="r1",
        code="NEWS_EARLY_DEMAND",
        params={"k": 3, "median_ratio": 3.3},
        statement="3 early videos on this ran a median 3.3x their channel's normal",
        ref_video_ids=[ref.video_id for ref in refs],
        source=EvidenceSource.youtube_public,
    )
    fit = Reason(
        reason_id="f1",
        code="NEWS_OFFERING_LINK",
        params={"offering": "Knee replacement"},
        statement="Links to your knee replacement service",
        source=EvidenceSource.news,
    )
    stable = make_stable_key(CLIENT_ID, RecType.news_opportunity, str(signal), window)
    card = RecommendationDraft(
        draft_id=f"d_{stable[:16]}",
        stable_key=stable,
        client_id=CLIENT_ID,
        type=RecType.news_opportunity,
        window_days=window,
        as_of=AS_OF,
        produced_by="news_opportunity@1",
        subject=subject,
        topic_ids=[topic_id("kw_knee")],
        reference_pool=refs,
        reasons=[demand],
        fit_reasons=[fit],
        confidence=Confidence(
            label=ConfidenceLabel.medium,
            outliers=3,
            channels=3,
            matching_videos=3,
            rule_id=ConfidenceRuleId.news_v1,
        ),
    )
    early_signal_id = uuid.uuid5(CLIENT_ID, "news-ai-triage")
    early_subject = subject.model_copy(
        update={
            "news_signal_id": early_signal_id,
            "key_entity": "AI triage pilot",
            "angle": "What the new AI triage pilot in Chennai emergency rooms means for patients",
        }
    )
    early_stable = make_stable_key(CLIENT_ID, RecType.news_opportunity, str(early_signal_id), window)
    early = card.model_copy(
        update={
            "draft_id": f"d_{early_stable[:16]}",
            "stable_key": early_stable,
            "subject": early_subject,
            "reference_pool": [],
            "reasons": [
                demand.model_copy(update={"params": {}, "statement": "Reported by 2 independent outlets"})
            ],
            "confidence": Confidence(
                label=ConfidenceLabel.low,
                outliers=0,
                channels=0,
                basis=ConfidenceBasis.first_mover,
                rule_id=ConfidenceRuleId.news_v1,
            ),
        }
    )
    return [card, early]


def insertion(text: str, keyword: str) -> InsertionOp:
    return InsertionOp(
        op_id="op1",
        kind=InsertionKind.insert,
        position=InsertionPosition.after_first_paragraph,
        anchor=InsertionAnchor(strategy="paragraph", anchor_excerpt="Knee pain causes", anchor_hash="h1"),
        text=text,
        text_bytes=len(text.encode()),
        keywords_added=[keyword],
        claim_level=ClaimLevel.l1_context,
        phrased_by="template",
    )


def refresh_drafts(window: int) -> tuple[list[RecommendationDraft], list[DescriptionRefresh]]:
    op = insertion("Planning surgery? See our guide to knee replacement cost.", "knee replacement cost")
    subject = RefreshSubject(video_id=video_id("o2"), operations=[op], review_required=True)
    own_ref = reference(SPECS["o2"])
    refs = [own_ref, reference(SPECS["k1"]), reference(SPECS["k2"])]
    reason = Reason(
        reason_id="r1",
        code="KEYWORD_DEMAND",
        params={"k": 2},
        statement="2 recent outliers use this keyword in their titles",
        ref_video_ids=[ref.video_id for ref in refs[1:]],
        source=EvidenceSource.youtube_public,
    )
    fit = Reason(
        reason_id="f1",
        code="KEYWORD_MATCH",
        params={"keyword": "knee replacement"},
        statement="Matches your keyword: knee replacement",
        source=EvidenceSource.youtube_public,
    )
    stable = make_stable_key(CLIENT_ID, RecType.description_refresh, video_id("o2"), window)
    card = RecommendationDraft(
        draft_id=f"d_{stable[:16]}",
        stable_key=stable,
        client_id=CLIENT_ID,
        type=RecType.description_refresh,
        window_days=window,
        as_of=AS_OF,
        produced_by="description_refresh@1",
        subject=subject,
        topic_ids=[topic_id("kw_knee")],
        reference_pool=refs,
        reasons=[reason],
        fit_reasons=[fit],
        confidence=Confidence(
            label=ConfidenceLabel.medium,
            outliers=2,
            channels=2,
            matching_videos=3,
            rule_id=ConfidenceRuleId.refresh_v1,
        ),
    )
    hygiene_op = insertion("Book a consultation: https://www.apollohospitals.com/book", "booking link")
    hygiene_subject = RefreshSubject(video_id=video_id("o3"), operations=[hygiene_op], review_required=False)
    hygiene_stable = make_stable_key(CLIENT_ID, RecType.description_refresh, video_id("o3"), window)
    hygiene = card.model_copy(
        update={
            "draft_id": f"d_{hygiene_stable[:16]}",
            "stable_key": hygiene_stable,
            "subject": hygiene_subject,
            "reference_pool": [],
            "reasons": [
                reason.model_copy(update={"params": {}, "statement": "No booking link in the description"})
            ],
            "confidence": Confidence(
                label=ConfidenceLabel.low,
                outliers=0,
                channels=0,
                basis=ConfidenceBasis.rule_based,
                rule_id=ConfidenceRuleId.refresh_v1,
            ),
        }
    )
    details = DescriptionRefresh(
        id="ref_o2",
        client_id=CLIENT_ID,
        video_id=video_id("o2"),
        run_id=RUN_ID,
        triggers=[],
        operations=[op],
        inserted_bytes=op.text_bytes + 1,
        resulting_bytes=420,
        expected_effect="more search impressions for knee replacement cost",
        confidence=card.confidence,
        priority=1.0,
        expires_at=AS_OF + timedelta(days=14),
        status=RefreshStatus.proposed,
        description_hash_at_generation="abc",
        review_required=True,
        generated_at=AS_OF,
    )
    return [card, hygiene], [details]


def apollo_inputs(
    selected_window: int = 30, videos: list[VideoSpec] | None = None, **overrides: Any
) -> RecommendInputs:
    specs = VIDEOS if videos is None else videos
    windows = []
    for window in (7, 30, 90):
        age = COMPARISON_AGE[window]
        windows.append(
            WindowAnalytics(
                window_days=window,
                metrics={video_id(spec.key): video_metrics(spec, age) for spec in specs},
                traits={video_id(spec.key): video_traits(spec) for spec in specs},
                lifts=lifts(window),
                gap_matrix=gap_matrix(window, specs),
                own_report=own_report(window, specs),
            )
        )
    fields: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "as_of": AS_OF,
        "selected_window": selected_window,
        "client": ClientFacts(
            name="Apollo Hospitals",
            offerings=["Knee replacement", "Angioplasty", "Kidney transplant"],
            locations=["Chennai", "Delhi"],
            website_domains=["apollohospitals.com"],
            brand_aliases=["Apollo"],
            niche_keywords=["knee replacement", "angioplasty", "heart attack", "spine surgery"],
            vertical="hospital",
            domain_kind="healthcare",
        ),
        "buyer_questions": [
            buyer_question(topic) for topic in TOPICS if topic.kind is TopicKind.buyer_question
        ],
        "topic_refs": [topic_ref(topic) for topic in TOPICS],
        "topic_matches": topic_matches(specs),
        "directness": directness(specs),
        "own_channel_id": channel_id("apollo"),
        "competitor_set": CompetitorSet(
            client_id=CLIENT_ID,
            version=1,
            competitor_channel_ids=[channel_id(key) for key in COMPETITOR_KEYS],
            niche_channel_ids=[channel_id(spec.key) for spec in CHANNELS if spec.role is ChannelRole.niche],
            confirmed_by="user",
            confirmed_at=AS_OF - timedelta(days=10),
        ),
        "channels": [channel_record(spec) for spec in CHANNELS],
        "channel_roles": {channel_id(spec.key): spec.role for spec in CHANNELS},
        "videos": [video_record(spec) for spec in specs],
        "niche_search_hits": {
            channel_id(key): hits
            for key, hits in (("drheart", 6), ("ortho", 4), ("healthdesk", 3), ("practo", 8), ("kidsfun", 1))
        },
        "analytics": windows,
        "data_refreshed_at": AS_OF - timedelta(hours=6),
    }
    # 04/08/09 fixtures reference the apollo channels; a test replacing the niche passes its own
    if "competitor_candidates" not in overrides:
        fields["competitor_candidates"] = default_candidates()
    if "news_drafts" not in overrides:
        fields["news_drafts"] = [draft for window in (7, 30, 90) for draft in news_drafts(window)]
    if "refresh_drafts" not in overrides:
        pairs = [refresh_drafts(window) for window in (7, 30, 90)]
        fields["refresh_drafts"] = [draft for drafts, _ in pairs for draft in drafts]
        fields["refreshes"] = pairs[0][1]
    fields.update(overrides)
    return RecommendInputs(**fields)


def default_candidates() -> list[CompetitorCandidate]:
    return [
        competitor_candidate("fortis", domain="fortishealthcare.com", aliases=["Fortis"]),
        competitor_candidate("narayana", domain="narayanahealth.org"),
        competitor_candidate("medanta", domain="medanta.org"),
        competitor_candidate("maxhc", domain="maxhealthcare.in"),
        competitor_candidate("manipal", domain="manipalhospitals.com"),
        competitor_candidate(
            "practo", role=CandidateRole.niche_set, status=CandidateStatus.rejected, domain="practo.com"
        ),
    ]
