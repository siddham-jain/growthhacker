from collections.abc import Callable
from datetime import timedelta

from app.adapters.freshness.base import FreshnessDeps, build_source
from app.core.config import Settings
from app.core.container import Container
from app.core.db import UnitOfWork
from app.core.redis import QuotaCounter
from app.core.registry import freshness_source_registry
from app.ports.embeddings import Embedder
from app.ports.llm import LLMClient
from app.ports.youtube import YouTubeGateway
from app.services.news.context import NewsClientContext
from app.services.news.demand import ChannelBaselines, NewsSearchReserve, YouTubeDemandChecker
from app.services.news.history import NewsReactiveHistory
from app.services.news.judge import NewsRelevanceJudge
from app.services.news.normalize import ItemNormalizer
from app.services.news.packs import SourcePackRegistry, default_pack_registry
from app.services.news.poller import NewsPoller, SourceBreaker
from app.services.news.repository import CachedVideoSearch, DemandCheckStore
from app.services.news.scanner import NewsScanner
from app.services.news.service import NewsRadarService
from app.services.news.tiers import SourceTierResolver


def scanner_factory(
    settings: Settings,
    packs: SourcePackRegistry,
    *,
    llm: LLMClient,
    gateway: YouTubeGateway | None,
    baselines: ChannelBaselines,
    quota_counter: QuotaCounter | None,
) -> Callable[[NewsClientContext, UnitOfWork], NewsScanner]:
    judge = NewsRelevanceJudge(llm, settings.news.judge_concurrency)
    history = NewsReactiveHistory(
        settings.news.own_history_min_videos, settings.news.own_history_lookback_days
    )
    reserve = NewsSearchReserve(quota_counter, settings.quota) if quota_counter is not None else None

    def build(ctx: NewsClientContext, uow: UnitOfWork) -> NewsScanner:
        demand = YouTubeDemandChecker(
            gateway=gateway,
            local=CachedVideoSearch(uow.session),
            cache=DemandCheckStore(uow.session),
            reserve=reserve,
            baselines=baselines,
            news=settings.news,
            analytics=settings.analytics,
        )
        return NewsScanner(
            settings=settings.news,
            packs=packs.resolve(ctx.freshness.source_packs),
            judge=judge,
            demand=demand,
            history=history,
        )

    return build


def build_news_radar(
    container: Container,
    *,
    llm: LLMClient,
    gateway: YouTubeGateway | None,
    baselines: ChannelBaselines,
    embedder: Embedder | None = None,
    packs: SourcePackRegistry | None = None,
) -> NewsRadarService:
    settings = container.settings
    packs = packs or default_pack_registry()
    deps = FreshnessDeps(
        http=container.http, settings=settings, clock=container.clock, rate_limiter=container.rate_limiter
    )
    sources = {key: build_source(key, deps) for key in freshness_source_registry}
    every_pack = packs.resolve(packs.keys())
    normalizer = ItemNormalizer(
        packs.tiers,
        SourceTierResolver(packs.tiers, every_pack),
        timedelta(hours=settings.news.future_date_tolerance_hours),
    )
    poller = NewsPoller(
        sources=sources,
        normalizer=normalizer,
        breaker=SourceBreaker(container.redis, settings.redis.key_prefix, settings.news),
        budget=container.quota_counter,
        locks=container.lock_manager,
        news=settings.news,
        tavily=settings.tavily,
    )
    return NewsRadarService(
        settings=settings,
        uow=container.uow,
        redis=container.redis,
        packs=packs,
        poller=poller,
        scanner_for=scanner_factory(
            settings,
            packs,
            llm=llm,
            gateway=gateway,
            baselines=baselines,
            quota_counter=container.quota_counter,
        ),
        embedder=embedder,
    )
