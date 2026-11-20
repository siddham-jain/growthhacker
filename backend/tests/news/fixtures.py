import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import AnalyticsSettings, NewsSettings
from app.domain.common import VideoFormat
from app.domain.news import ConfirmationStatus, EventType, NewsItem, RelevanceBand, SafetyFlag
from app.domain.profile import (
    BusinessIdentity,
    BusinessModel,
    BusinessProfile,
    BuyerQuestion,
    BuyerQuestionIntent,
    BuyerQuestionSourceKind,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    ConversionAction,
    FreshnessProfile,
    FreshnessSensitivity,
    FunnelStage,
    Offering,
    ProfileQuality,
    Prominence,
    RefreshPolicy,
    ReviewStatus,
    SeasonalWindow,
    ValueProposition,
    WatchEntity,
)
from app.domain.youtube import SearchHit, SearchPage, VideoRecord
from app.ports.freshness import RawNewsItem
from app.ports.llm import LLMError, LLMResult, TokenUsage
from app.services.news.clustering import StoryClusterer
from app.services.news.context import NewsClientContext
from app.services.news.demand import CandidateVideo, DemandOutcome, YouTubeDemandChecker
from app.services.news.history import NewsReactiveHistory
from app.services.news.judge import EntityLLM, NewsJudgementLLM, NewsRelevanceJudge, OfferingLinkLLM
from app.services.news.normalize import ItemNormalizer
from app.services.news.packs import default_pack_registry
from app.services.news.prefilter import StoryBundle
from app.services.news.scanner import NewsScanner
from app.services.news.tiers import SourceTierResolver

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
WORKSPACE_ID = uuid.UUID("0190f2a4-0000-7000-8000-000000000001")
RUNABLE_ID = uuid.UUID("0190f2a4-0000-7000-8000-0000000000a1")
APOLLO_ID = uuid.UUID("0190f2a4-0000-7000-8000-0000000000b2")
QUESTION_SLIDES = uuid.UUID("0190f2a4-0000-7000-8000-00000000c001")
QUESTION_DENGUE = uuid.UUID("0190f2a4-0000-7000-8000-00000000c002")
RUNABLE_CHANNEL = "UCrunableOfficial00000000"[:24]
NEWS_SETTINGS = NewsSettings()
ANALYTICS = AnalyticsSettings()


def meta() -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.user,
        status=ClaimStatus.confirmed,
        confidence=1.0,
        first_seen_version=1,
        updated_at=NOW,
    )


def _identity(
    name: str, niche: str, industry: str, country: str, languages: list[str], **extra: Any
) -> BusinessIdentity:
    return BusinessIdentity(
        name=name,
        name_meta=meta(),
        one_liner=f"{name}: {niche}",
        one_liner_meta=meta(),
        industry=industry,
        industry_meta=meta(),
        niche=niche,
        business_model=extra.pop("business_model", BusinessModel.b2c_app),
        geo_scope=extra.pop("geo_scope", "global_online"),
        primary_language=languages[0],
        languages=languages,
        country=country,
        timezone=extra.pop("timezone", "UTC"),
        website=None,
        website_domains=extra.pop("website_domains", []),
        **extra,
    )


def _freshness(sensitivity: FreshnessSensitivity, **fields: Any) -> FreshnessProfile:
    return FreshnessProfile(
        sensitivity=sensitivity,
        sensitivity_score=0.8,
        score_components={},
        rationale="fixture",
        refresh_policy=RefreshPolicy(
            news_scan_interval_hours=6,
            refresh_check_interval_days=7,
            description_refresh_eligible=True,
            min_signal_strength=0.5,
        ),
        meta=meta(),
        **fields,
    )


def _action(
    action_id: str, kind: str, label: str, target: str, primary: bool, offering_ids: list[str]
) -> ConversionAction:
    return ConversionAction(
        id=action_id,
        type=kind,
        label=label,
        target=target,
        medium="web",
        prominence=Prominence(pages_seen=5, on_home=True, in_header=True, in_footer=False),
        is_primary=primary,
        offering_ids=offering_ids,
        meta=meta(),
    )


def _quality() -> ProfileQuality:
    return ProfileQuality(
        claims_total=10,
        claims_verified=10,
        claims_dropped_unverified=0,
        injection_flags=0,
        thin_site=False,
        label="rich",
        completeness=0.9,
    )


def _question(
    question_id: uuid.UUID, client_id: uuid.UUID, text: str, offering_ids: list[str]
) -> BuyerQuestion:
    return BuyerQuestion(
        id=question_id,
        client_id=client_id,
        text=text,
        normalized_text=text.casefold(),
        language="en",
        funnel_stage=FunnelStage.consideration,
        intent=BuyerQuestionIntent.comparison,
        offering_ids=offering_ids,
        source=BuyerQuestionSourceKind.website_faq,
        status=ReviewStatus.confirmed,
        priority=1,
        meta=meta(),
    )


def runable_profile() -> BusinessProfile:
    return BusinessProfile(
        profile_id=uuid.uuid4(),
        client_id=RUNABLE_ID,
        version=3,
        status="confirmed",
        generated_at=NOW,
        identity=_identity(
            "Runable",
            "AI agent workspace for slides and websites",
            "ai_software",
            "US",
            ["en"],
            business_model=BusinessModel.b2b_saas,
            website_domains=["runable.com"],
        ),
        offerings=[
            Offering(
                id="off_slides",
                name="AI presentations",
                kind="feature",
                is_primary=True,
                priority=3,
                meta=meta(),
            ),
            Offering(id="off_sites", name="AI website builder", kind="feature", meta=meta()),
        ],
        value_propositions=[
            ValueProposition(
                id="vp_models",
                statement="Runs on the latest frontier models",
                kind="technology",
                offering_ids=["off_slides"],
                meta=meta(),
            )
        ],
        conversion_actions=[
            _action("act_signup", "signup", "Start free", "https://runable.com/signup", True, ["off_slides"])
        ],
        freshness=_freshness(
            FreshnessSensitivity.very_high,
            watch_entities=[
                WatchEntity(
                    name="Claude",
                    aliases=["Anthropic"],
                    kind="model",
                    relation_to_client="supplier",
                    importance=3,
                    meta=meta(),
                ),
                WatchEntity(
                    name="GPT",
                    aliases=["OpenAI"],
                    kind="model",
                    relation_to_client="supplier",
                    importance=2,
                    meta=meta(),
                ),
            ],
            source_packs=["ai_frontier"],
            regions=["US"],
            languages=["en"],
        ),
        quality=_quality(),
    )


def apollo_profile() -> BusinessProfile:
    return BusinessProfile(
        profile_id=uuid.uuid4(),
        client_id=APOLLO_ID,
        version=2,
        status="confirmed",
        generated_at=NOW,
        identity=_identity(
            "Apollo Hospitals",
            "multi-speciality hospitals in India",
            "hospital",
            "IN",
            ["en", "hi"],
            business_model=BusinessModel.multi_location_service,
            geo_scope="national",
            timezone="Asia/Kolkata",
            website_domains=["apollohospitals.com"],
        ),
        offerings=[
            Offering(id="off_er", name="Emergency care", kind="department", is_primary=True, meta=meta()),
            Offering(id="off_im", name="Internal medicine", kind="specialty", meta=meta()),
        ],
        value_propositions=[
            ValueProposition(id="vp_24x7", statement="24x7 emergency care", kind="accessibility", meta=meta())
        ],
        conversion_actions=[
            _action("act_buy", "buy", "Buy medicines online", "https://apollo.example/pharmacy", False, []),
            _action(
                "act_book",
                "book_appointment",
                "Book a consultation",
                "https://apollo.example/book",
                True,
                ["off_er"],
            ),
        ],
        freshness=_freshness(
            FreshnessSensitivity.medium,
            watch_entities=[
                WatchEntity(
                    name="MoHFW",
                    aliases=["Health Ministry"],
                    kind="regulator",
                    relation_to_client="regulator",
                    meta=meta(),
                )
            ],
            seasonality=[
                SeasonalWindow(
                    label="dengue season",
                    months=[6, 7, 8, 9, 10, 11],
                    geo="IN",
                    query_terms=["dengue advisory"],
                    meta=meta(),
                )
            ],
            source_packs=["health_in"],
            regions=["IN"],
            languages=["en", "hi"],
            regulated_domain=True,
            domain_kind="healthcare",
        ),
        quality=_quality(),
    )


def video_record(
    video_id: str, channel_id: str, title: str, published_at: datetime, views: int, **extra: Any
) -> VideoRecord:
    return VideoRecord(
        video_id=video_id,
        channel_id=channel_id,
        channel_title=extra.pop("channel_title", f"channel {channel_id[-4:]}"),
        published_at=published_at,
        effective_published_at=published_at,
        title=title,
        description=extra.pop("description", ""),
        content_hash="h",
        description_hash="h",
        duration_s=extra.pop("duration_s", 300),
        format=VideoFormat.long_form,
        format_confidence="high",
        view_count=views,
        ingest_source="search",
        metadata_fetched_at=NOW,
        **extra,
    )


def runable_context(**overrides: Any) -> NewsClientContext:
    profile = overrides.pop("profile", runable_profile())
    return NewsClientContext(
        workspace_id=overrides.pop("workspace_id", WORKSPACE_ID),
        client_id=overrides.pop("client_id", RUNABLE_ID),
        profile=profile,
        buyer_questions=[
            _question(QUESTION_SLIDES, RUNABLE_ID, "Which AI model makes the best slides?", ["off_slides"])
        ],
        own_channel_id=overrides.pop("own_channel_id", RUNABLE_CHANNEL),
        own_videos=overrides.pop(
            "own_videos",
            [
                video_record(
                    "ownvideo001",
                    RUNABLE_CHANNEL,
                    "How to make money using AI in 2026",
                    NOW - timedelta(days=40),
                    9_000,
                )
            ],
        ),
        own_subscriber_count=20_000,
        **overrides,
    )


def apollo_context(**overrides: Any) -> NewsClientContext:
    return NewsClientContext(
        workspace_id=WORKSPACE_ID,
        client_id=APOLLO_ID,
        profile=apollo_profile(),
        buyer_questions=[
            _question(QUESTION_DENGUE, APOLLO_ID, "When should I go to hospital for dengue?", ["off_er"])
        ],
        **overrides,
    )


PACKS = default_pack_registry()


def normalizer() -> ItemNormalizer:
    every = PACKS.resolve(PACKS.keys())
    return ItemNormalizer(PACKS.tiers, SourceTierResolver(PACKS.tiers, every), timedelta(hours=6))


def news_item(
    url: str,
    title: str,
    snippet: str = "",
    published_at: datetime | None = None,
    *,
    source_key: str = "tavily_news",
    fetched_at: datetime = NOW,
    feed_tier: Any = None,
) -> NewsItem:
    outcome = normalizer().normalize(
        RawNewsItem(url=url, title=title, snippet=snippet, published_at=published_at),
        source_key=source_key,
        fetched_at=fetched_at,
        feed_tier=feed_tier,
    )
    assert outcome.item is not None, outcome.dropped_reason
    return outcome.item


def clusterer() -> StoryClusterer:
    return StoryClusterer(
        fuzzy_min_ratio=NEWS_SETTINGS.fuzzy_title_min_ratio,
        fuzzy_window=timedelta(hours=72),
        embedding_min_cosine=0.86,
        developing_window=timedelta(hours=12),
    )


def bundles(items: Sequence[NewsItem], now: datetime = NOW) -> list[StoryBundle]:
    result = clusterer().cluster(items, now)
    members: dict[uuid.UUID, list[NewsItem]] = {}
    for item in items:
        story_id = result.assignments[item.id]
        members.setdefault(story_id, []).append(item.model_copy(update={"story_id": story_id}))
    return [StoryBundle(story, members[story.id]) for story in result.stories]


def entity(text: str, ref: str, quote: str | None = None, kind: str = "model") -> EntityLLM:
    return EntityLLM(text=text, kind=kind, item_ref=ref, quote=quote or text)


def judgement(
    key: EntityLLM,
    *,
    band: RelevanceBand = RelevanceBand.direct,
    event: EventType = EventType.model_release,
    confirmation: ConfirmationStatus = ConfirmationStatus.confirmed,
    offerings: Sequence[tuple[str, list[str]]] = (("o1", ["v1"]),),
    questions: Sequence[str] = ("q1",),
    action: str | None = "a1",
    region: str = "global",
    flags: Sequence[SafetyFlag] = (),
    others: Sequence[EntityLLM] = (),
    event_date: date | None = None,
    angle: str = "Show the news inside the product",
    about_client: bool = False,
    conflicting: bool = False,
) -> NewsJudgementLLM:
    return NewsJudgementLLM(
        relevance_band=band,
        key_entity=key,
        other_entities=list(others),
        event_type=event,
        event_date=event_date,
        confirmation=confirmation,
        offering_links=[
            OfferingLinkLLM(offering_ref=o, value_prop_refs=v, rationale="fits") for o, v in offerings
        ],
        buyer_question_refs=list(questions),
        angle=angle,
        region_relevance=region,
        about_client=about_client,
        safety_flags=list(flags),
        conflicting_reports=conflicting,
        conversion_action_ref=action,
    )


class ScriptedLLM:
    """returns the scripted judgement whose key text appears in the story; records every call."""

    def __init__(self, script: dict[str, NewsJudgementLLM | LLMError]) -> None:
        self.script = script
        self.calls: list[Any] = []

    async def generate_structured(self, *, task, schema, system, input, context, tier=None, budget=None):
        self.calls.append(input)
        text = " ".join(block.text for block in input.untrusted)
        for marker, response in self.script.items():
            if marker in text:
                if isinstance(response, LLMError):
                    raise response
                return LLMResult[schema](
                    value=response,
                    raw_text="{}",
                    task=task,
                    prompt_id=system.prompt_id,
                    prompt_version="v1",
                    provider="fake",
                    model="claude-sonnet-5-5",
                    route_index=0,
                    attempts=1,
                    repaired=False,
                    usage=TokenUsage(input_tokens=1200, output_tokens=300),
                    cost_usd=0.006,
                    latency_ms=5,
                    stop_reason="end_turn",
                )
        raise AssertionError(f"no scripted judgement for: {text[:120]}")

    async def generate_text(self, **_: Any) -> Any:
        raise NotImplementedError


class FakeGateway:
    def __init__(self, videos: Sequence[VideoRecord] = ()) -> None:
        self.videos = {v.video_id: v for v in videos}
        self.searches: list[Any] = []

    async def search(self, query, page_token, ctx) -> SearchPage:
        self.searches.append(query)
        items = [
            SearchHit(kind="video", id=v.video_id, channel_id=v.channel_id, rank=i)
            for i, v in enumerate(self.videos.values())
        ]
        return SearchPage(
            query_key=query.q,
            items=items,
            fetched_at=NOW,
            expires_at=NOW + timedelta(hours=6),
            from_cache=False,
        )

    async def videos_by_id(self, ids, ctx, with_localizations: bool = False):
        from app.ports.youtube import VideoBatch

        return VideoBatch(found=[self.videos[i] for i in ids if i in self.videos])


class FakeBaselines:
    def __init__(
        self, baselines: dict[str, float] | None = None, subscribers: dict[str, int] | None = None
    ) -> None:
        self.baselines = baselines or {}
        self.subscribers = subscribers or {}

    async def views_at_age(self, channel_id: str, age_days: int, as_of: datetime) -> float | None:
        return self.baselines.get(channel_id)

    async def subscriber_count(self, channel_id: str) -> int | None:
        return self.subscribers.get(channel_id)


class MemoryDemandCache:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, date], DemandOutcome] = {}

    async def get(self, entity_norm: str, not_before: datetime) -> DemandOutcome | None:
        hits = [
            o
            for (key, _day), o in self.rows.items()
            if key == entity_norm and o.check.checked_at >= not_before
        ]
        return max(hits, key=lambda o: o.check.checked_at, default=None)

    async def put(self, entity_norm: str, day: date, query: str, outcome: DemandOutcome) -> None:
        self.rows[(entity_norm, day)] = outcome


class NoLocalVideos:
    def __init__(self, videos: Sequence[CandidateVideo] = ()) -> None:
        self.videos = list(videos)

    async def find(self, terms, published_after, limit) -> list[CandidateVideo]:
        return self.videos


class FakeReserve:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.reserved = 0
        self.refunded = 0

    async def try_reserve(self, client_id, client_cap, now) -> bool:
        if self.allow:
            self.reserved += 1
        return self.allow

    async def refund(self, client_id, now) -> None:
        self.refunded += 1


def demand_checker(
    gateway: FakeGateway | None = None,
    baselines: FakeBaselines | None = None,
    *,
    reserve: FakeReserve | None = None,
    cache: MemoryDemandCache | None = None,
    local: NoLocalVideos | None = None,
    settings: NewsSettings = NEWS_SETTINGS,
) -> YouTubeDemandChecker:
    return YouTubeDemandChecker(
        gateway=gateway,
        local=local or NoLocalVideos(),
        cache=cache or MemoryDemandCache(),
        reserve=reserve or FakeReserve(),
        baselines=baselines or FakeBaselines(),
        news=settings,
        analytics=ANALYTICS,
    )


def scanner(
    ctx: NewsClientContext,
    llm: ScriptedLLM,
    demand: YouTubeDemandChecker,
    settings: NewsSettings = NEWS_SETTINGS,
) -> NewsScanner:
    return NewsScanner(
        settings=settings,
        packs=PACKS.resolve(ctx.freshness.source_packs),
        judge=NewsRelevanceJudge(llm, 5),
        demand=demand,
        history=NewsReactiveHistory(3, 365),
    )
