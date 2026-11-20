import itertools
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.adapters.llm.circuit_breaker import CircuitBreaker, MemoryCircuitStore
from app.adapters.llm.client import DefaultLLMClient
from app.adapters.llm.fallback_chain import FallbackChain
from app.adapters.llm.limiter import AdaptiveLimiter, RunLimiter
from app.adapters.llm.providers.base import ChatProvider
from app.adapters.llm.retry_policy import RetryPolicy
from app.core.clock import FrozenClock
from app.core.config import LLMSettings
from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.common import ChannelRole, SubscriberBand
from app.domain.confidence import Confidence, ConfidenceLabel
from app.domain.news import NewsSourceRef
from app.domain.recommendations import (
    CreatorConsistency,
    CreatorPerformance,
    CreatorRelevance,
    CreatorSubject,
    EvidenceNumber,
    ExpectedRange,
    Lens,
    LensAssignment,
    NewsCompliance,
    NewsSubject,
    PatternSpec,
    Reason,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    VideoIdeaSubject,
    make_stable_key,
)
from app.services.llm.budgets import MemoryBudgetLedger
from app.services.llm.costs import CostCalculator
from app.services.llm.prompts import PromptRegistry, prompt_registry
from app.services.llm.result_cache import MemoryResultCache
from app.services.llm.usage import MemoryUsageRecorder
from app.services.llm.writer.schemas import WriterClientContext

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
CLIENT_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-1a2b3c4d5e6f")
RUN_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-bbbbbbbbbbbb")
QUESTION_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-cccccccccccc")
CHANNEL_IDS = ("UCbrightSmileDental00000", "UCcityDentalCare00000000", "UCpearlClinicOfficial000")
VIDEO_IDS = ("aaaaaaaaaa1", "bbbbbbbbbb2", "cccccccccc3", "dddddddddd4")
CHANNELS = ("Bright Smile Dental", "City Dental Care", "Pearl Clinic")
TITLES = (
    "Teeth whitening price list 2026 at our clinic",
    "Is professional whitening worth it? Honest review",
    "Whitening strips vs clinic whitening",
)


def make_llm_settings(**overrides: Any) -> LLMSettings:
    data: dict[str, Any] = {
        "anthropic": {"api_key": "test-anthropic"},
        "openai_compat": {
            "api_key": "test-gateway",
            "base_url": "http://gateway.test/v1",
            "low": {"model": "gw-low"},
            "medium": {"model": "gw-medium"},
            "high": {"model": "gw-high"},
        },
    }
    data.update(overrides)
    return LLMSettings.model_validate(data)


class FakeMonotonic:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class Sleeps:
    def __init__(self, monotonic: FakeMonotonic | None = None) -> None:
        self.delays: list[float] = []
        self._monotonic = monotonic

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._monotonic is not None:
            self._monotonic.now += delay


def make_chain(
    providers: dict[str, ChatProvider],
    settings: LLMSettings | None = None,
    *,
    monotonic: FakeMonotonic | None = None,
    sleeps: Sleeps | None = None,
    breaker: CircuitBreaker | None = None,
) -> FallbackChain:
    settings = settings or make_llm_settings()
    monotonic = monotonic or FakeMonotonic()
    return FallbackChain(
        settings=settings,
        providers=providers,
        limiters={name: AdaptiveLimiter(4) for name in providers},
        run_limiter=RunLimiter(6),
        breaker=breaker or CircuitBreaker(settings.resilience, MemoryCircuitStore(), config_hash="test"),
        policy=RetryPolicy(settings.resilience),
        costs=CostCalculator(settings.prices),
        clock=FrozenClock(NOW),
        sleep=sleeps or Sleeps(monotonic),
        monotonic=monotonic,
    )


def make_client(
    providers: dict[str, ChatProvider],
    settings: LLMSettings | None = None,
    *,
    prompts: PromptRegistry = prompt_registry,
    **chain_options: Any,
) -> tuple[DefaultLLMClient, MemoryUsageRecorder, MemoryBudgetLedger]:
    settings = settings or make_llm_settings()
    usage = MemoryUsageRecorder()
    budget = MemoryBudgetLedger(settings.budgets)
    client = DefaultLLMClient(
        settings=settings,
        prompts=prompts,
        chain=make_chain(providers, settings, **chain_options),
        budget=budget,
        costs=CostCalculator(settings.prices),
        cache=MemoryResultCache(FrozenClock(NOW)),
        usage=usage,
        clock=FrozenClock(NOW),
    )
    return client, usage, budget


def views(age_days: int = 7, value: int = 18_400) -> ViewsAtAgeSummary:
    return ViewsAtAgeSummary(
        age_days=age_days, views=value, method=AgeMethod.observed, label=f"day {age_days}"
    )


def make_reference(index: int, **overrides: Any) -> ReferenceVideo:
    video_id = VIDEO_IDS[index]
    fields: dict[str, Any] = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": TITLES[index % len(TITLES)],
        "channel": CHANNELS[index % len(CHANNELS)],
        "channel_id": CHANNEL_IDS[index % len(CHANNEL_IDS)],
        "channel_role": ChannelRole.competitor,
        "published_at": NOW - timedelta(days=10 + index),
        "views_day7": 18_400,
        "views_at_age": views(),
        "outlier_ratio": 3.6,
        "is_outlier": True,
    }
    fields.update(overrides)
    return ReferenceVideo(**fields)


def number(value: float, unit: str, display: str) -> EvidenceNumber:
    return EvidenceNumber(value=value, unit=unit, display=display)  # type: ignore[arg-type]


def reason(
    reason_id: str, statement: str, numbers: list[EvidenceNumber], source: str = "youtube_public"
) -> Reason:
    return Reason(
        reason_id=reason_id,
        code="OUTLIER_SHARE",
        statement=statement,
        numbers=numbers,
        source=source,  # type: ignore[arg-type]
    )


DENTAL_REASONS = [
    reason(
        "r_share",
        "4 of 6 competitor outliers in the last 30 days answer a cost question",
        [number(4, "count", "4"), number(6, "count", "6"), number(30, "days", "30 days")],
    ),
    reason(
        "r_ratio",
        "Cost videos reached 3.6x their channel baseline at day 7",
        [number(3.6, "ratio_x", "3.6x"), number(7, "days", "day 7")],
    ),
    reason(
        "r_views",
        "The strongest cost video had 18.4K views at day 7",
        [number(18_400, "views", "18.4K"), number(7, "days", "day 7")],
    ),
]
DENTAL_FIT = [
    reason(
        "r_question",
        'Your buyers ask "How much does teeth whitening cost?" and your channel has no video answering it',
        [],
        source="client_config",
    )
]

_draft_counter = itertools.count(1)


def make_draft(
    rec_type: RecType = RecType.video_idea,
    *,
    reasons: list[Reason] | None = None,
    fit_reasons: list[Reason] | None = None,
    subject: Any = None,
    **overrides: Any,
) -> RecommendationDraft:
    reasons = DENTAL_REASONS if reasons is None else reasons
    fit_reasons = DENTAL_FIT if fit_reasons is None else fit_reasons
    if subject is None:
        subject = VideoIdeaSubject(topic_id="tr_whitening", topic_text="teeth whitening cost")
    draft_number = next(_draft_counter)
    fields: dict[str, Any] = {
        "draft_id": f"draft_{draft_number}",
        "stable_key": make_stable_key(CLIENT_ID, rec_type, f"subject-{draft_number}", 30),
        "client_id": CLIENT_ID,
        "type": rec_type,
        "window_days": 30,
        "as_of": NOW,
        "produced_by": "test",
        "subject": subject,
        "topic_ids": ["tr_whitening"],
        "buyer_question_id": QUESTION_ID,
        "references": [make_reference(i) for i in range(3)],
        "reasons": reasons,
        "fit_reasons": fit_reasons,
        "lenses": [LensAssignment(lens=Lens.views, reasons=reasons[:1])],
        "expected_range": ExpectedRange(
            low=1.2, high=2.0, basis="peer", comparison_age_days=7, display="1.2x-2.0x (indicative)"
        ),
        "confidence": Confidence(label=ConfidenceLabel.medium, outliers=4, channels=3),
    }
    fields.update(overrides)
    return RecommendationDraft(**fields)


def pattern_subject() -> PatternSpec:
    return PatternSpec(
        kind="title",
        traits={"question": "yes"},
        scope="competitor_set",
        lift=2.1,
        ci_low=1.4,
        ci_high=3.0,
        n_with=7,
        n_outliers_with=5,
        n_total=40,
        n_outliers_total=9,
    )


def creator_subject() -> CreatorSubject:
    return CreatorSubject(
        channel_id="UCsmileCoachCreator00000",
        title="Smile Coach Priya",
        url="https://www.youtube.com/channel/UCsmileCoachCreator00000",
        subscriber_band=SubscriberBand.from_10k_to_100k,
        uploads_90d=12,
        uploads_checked=12,
        relevance=CreatorRelevance(match_share=0.5, topics_covered=3),
        performance=CreatorPerformance(
            median_outlier_ratio=2.0,
            median_views_at_age=4000,
            niche_median_views_at_age=2000,
            comparison_age_days=7,
        ),
        consistency=CreatorConsistency(median_gap_days=7.0, gap_cv=0.3, hit_rate=0.4, last_upload_at=None),
    )


def news_subject() -> NewsSubject:
    return NewsSubject(
        news_signal_id=UUID("0190f2a4-6c1e-7d3a-9b2f-dddddddddddd"),
        story_id=UUID("0190f2a4-6c1e-7d3a-9b2f-eeeeeeeeeeee"),
        key_entity="Fluoride Advisory 2026",
        angle="what the new fluoride advisory means for whitening",
        urgency="this_week",
        expires_at=NOW + timedelta(days=7),
        reference_sources=[
            NewsSourceRef(url="https://news.example.org/fluoride", publisher="Example News", published_at=NOW)
        ],
        compliance=NewsCompliance(review_required=True),
    )


def make_client_context(**overrides: Any) -> WriterClientContext:
    fields: dict[str, Any] = {
        "name": "Smile Studio Dental",
        "vertical": "dental clinic",
        "language": "en",
        "offerings": ["Teeth whitening", "Dental implants"],
        "value_props": ["Same-day appointments"],
        "buyer_questions": {QUESTION_ID: "How much does teeth whitening cost?"},
        "locations": ["Pune"],
        "domains": ["smilestudio.in"],
        "recent_titles": ["A tour of our new Pune clinic"],
        "known_channel_names": [*CHANNELS, "Dr Mehta Dental", "Smile Coach Priya"],
        "topic_labels": {"tr_whitening": "teeth whitening cost"},
    }
    fields.update(overrides)
    return WriterClientContext(**fields)


GOOD_COPY: dict[str, Any] = {
    "summary": "Make a short explainer answering how much teeth whitening costs at your clinic",
    "working_title": "What Changes the Price of Teeth Whitening?",
    "why_it_works": [
        {
            "fact_refs": ["f1"],
            "text": "4 of 6 competitor outliers in the last 30 days answer a cost question",
        },
        {"fact_refs": ["f2"], "text": "Cost videos reached 3.6x their channel baseline at day 7"},
    ],
    "fit_to_client": {
        "fact_refs": ["f4"],
        "text": 'Your buyers ask "How much does teeth whitening cost?" and no video of yours answers it yet',
    },
}
