from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.db import UnitOfWork
from app.core.redis import LockManager, QuotaCounter
from app.models.news import NewsQuery, YtDemandCheck
from app.ports.freshness import NewsFetchResult, RawNewsItem
from app.services.news.demand import YouTubeDemandChecker
from app.services.news.poller import NewsPoller, SourceBreaker
from app.services.news.query_planner import QueryPlanner
from app.services.news.repository import CachedVideoSearch, DemandCheckStore, NewsQueryStore, NewsSignalStore
from app.services.news.service import NewsRadarService
from tests.conftest import make_settings
from tests.foundation.factories import create_tenant
from tests.news.fixtures import (
    ANALYTICS,
    NEWS_SETTINGS,
    NOW,
    PACKS,
    FakeBaselines,
    FakeGateway,
    FakeReserve,
    ScriptedLLM,
    normalizer,
    runable_context,
    scanner,
)
from tests.news.test_golden_runable import CHANNELS, early_videos, script

pytestmark = pytest.mark.db
LATER = NOW + timedelta(hours=7)
LAUNCH = [
    RawNewsItem(
        url="https://www.anthropic.com/news/claude-fable-5-2",
        title="Introducing Claude Fable 5.2",
        snippet="Today we're releasing Claude Fable 5.2, our most capable model for slides.",
        published_at=NOW - timedelta(hours=14),
    ),
    RawNewsItem(
        url="https://techcrunch.com/anthropic-releases-claude-fable-5-2",
        title="Anthropic releases Claude Fable 5.2 with stronger agent skills",
        snippet="Anthropic on Thursday released Claude Fable 5.2.",
        published_at=NOW - timedelta(hours=12),
    ),
]
RETRACTION = RawNewsItem(
    url="https://www.reuters.com/technology/anthropic-retracts-fable",
    title="Anthropic retracts Claude Fable 5.2 announcement",
    snippet="The company said the Claude Fable 5.2 post went out in error.",
    published_at=LATER - timedelta(hours=1),
)


class SwitchableSource:
    def __init__(self, key: str) -> None:
        self.key = key
        self.items: list[RawNewsItem] = []
        self.calls = 0

    async def fetch(self, query, since):
        self.calls += 1
        return NewsFetchResult(
            items=list(self.items), cost=1.0 if self.key.startswith("tavily") else 0.0, status="ok"
        )

    async def health(self):
        raise NotImplementedError


@pytest.fixture
async def tenant(session):
    return await create_tenant(session, "runable")


def build_service(session_factory, redis, prefix, sources):
    settings = make_settings(redis={"url": "redis://unused", "key_prefix": prefix})
    poller = NewsPoller(
        sources=sources,
        normalizer=normalizer(),
        breaker=SourceBreaker(redis, prefix, settings.news),
        budget=QuotaCounter(redis, prefix=prefix, project="test"),
        locks=LockManager(redis, prefix=prefix),
        news=settings.news,
        tavily=settings.tavily,
    )
    llm = ScriptedLLM(script())
    gateway = FakeGateway(early_videos(2, 1))

    def scanner_for(ctx, uow):
        demand = YouTubeDemandChecker(
            gateway=gateway,
            local=CachedVideoSearch(uow.session),
            cache=DemandCheckStore(uow.session),
            reserve=FakeReserve(),
            baselines=FakeBaselines({c: 5_000.0 for c in CHANNELS}),
            news=NEWS_SETTINGS,
            analytics=ANALYTICS,
        )
        return scanner(ctx, llm, demand)

    service = NewsRadarService(
        settings=settings,
        uow=lambda: UnitOfWork(session_factory),
        redis=redis,
        packs=PACKS,
        poller=poller,
        scanner_for=scanner_for,
    )
    return service, llm


async def test_subscriptions_share_queries_across_clients_and_orphan_on_replan(session, tenant):
    other = await create_tenant(session, "slidesco")
    queries = NewsQueryStore(session)
    ctx = runable_context()
    plan = QueryPlanner(NEWS_SETTINGS).plan(ctx, PACKS.resolve(["ai_frontier"]), NOW)
    entity = [s for s in plan.subscriptions if s.origin == "entity"]
    await queries.sync_subscriptions(tenant, entity, 3, NOW)
    await queries.sync_subscriptions(other, entity, 1, NOW)
    counts = (await session.execute(select(NewsQuery.subscriber_count, NewsQuery.orphaned_at))).all()
    assert {row.subscriber_count for row in counts} == {2} and len(counts) == len(entity)
    await queries.sync_subscriptions(tenant, [], 4, NOW)
    await queries.sync_subscriptions(other, [], 2, NOW)
    orphaned = (
        await session.execute(select(func.count()).where(NewsQuery.orphaned_at.is_not(None)))
    ).scalar_one()
    assert orphaned == len(entity)
    assert await queries.due(NOW, 10) == []


async def test_scan_persists_signals_demand_and_withdraws_on_retraction(
    session, session_factory, tenant, redis, redis_prefix
):
    sources = {
        key: SwitchableSource(key)
        for key in ("tavily_news", "tavily_domains", "rss", "arxiv", "hf_trending", "gdelt")
    }
    sources["tavily_news"].items = LAUNCH
    service, llm = build_service(session_factory, redis, redis_prefix, sources)
    ctx = runable_context(workspace_id=tenant.workspace_id, client_id=tenant.client_id)

    report = await service.scan(ctx, now=NOW, force=True)

    assert len(report.early_signal_drafts) == 1 and report.recommendation_drafts == []
    assert report.early_signal_drafts[0].subject.key_entity == "Claude Fable 5.2"
    assert report.tavily_credits > 0 and report.llm_calls == 1
    store = NewsSignalStore(session, tenant)
    active = await store.active_for_client(NOW)
    assert [s.entity for s in active] == ["Claude Fable 5.2"]
    assert active[0].sources[0].publisher == "anthropic.com"
    demand_rows = (await session.execute(select(func.count()).select_from(YtDemandCheck))).scalar_one()
    assert demand_rows == 1

    sources["tavily_news"].items = [RETRACTION]
    summary = await service.poll_due(LATER)
    assert summary.items == 1
    second = await service.scan(ctx, now=LATER)

    assert second.drafts == [] and len(llm.calls) == 1
    assert [w.verification_status for w in second.withdrawals] == ["retracted"]
    assert await store.active_for_client(LATER) == []
    withdrawn = await store.withdrawn_since(LATER - timedelta(minutes=1))
    assert [s.verification_status for s in withdrawn] == ["retracted"]
    assert await store.enqueue_review(withdrawn[0].id, "check") is True
    assert await store.enqueue_review(withdrawn[0].id, "check") is False


async def test_forced_scans_are_rate_limited_per_client(
    session, session_factory, tenant, redis, redis_prefix
):
    sources = {"tavily_news": SwitchableSource("tavily_news")}
    service, _llm = build_service(session_factory, redis, redis_prefix, sources)
    ctx = runable_context(workspace_id=tenant.workspace_id, client_id=tenant.client_id)
    await service.scan(ctx, now=NOW, force=True)
    calls = sources["tavily_news"].calls
    await service.scan(ctx, now=NOW + timedelta(minutes=5), force=True)
    assert calls > 0 and sources["tavily_news"].calls == calls
