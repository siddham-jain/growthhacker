import uuid
from datetime import timedelta

import pytest

from app.core.config import NewsSettings, TavilySettings
from app.core.redis import LockManager, QuotaCounter
from app.ports.freshness import NewsFetchResult, PlannedQuery, RawNewsItem
from app.services.news.poller import NewsPoller, SourceBreaker
from app.services.news.repository import DueQuery
from tests.news.fixtures import NOW, normalizer

pytestmark = pytest.mark.usefixtures("redis_prefix")


class FakeSource:
    def __init__(self, key: str, results: list[NewsFetchResult]) -> None:
        self.key = key
        self.results = results
        self.calls: list[PlannedQuery] = []

    async def fetch(self, query, since):
        self.calls.append(query)
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]

    async def health(self):
        raise NotImplementedError


def ok(*items: RawNewsItem, cost: float = 1.0) -> NewsFetchResult:
    return NewsFetchResult(items=list(items), cost=cost, status="ok")


def due(source_key="tavily_news", weight=0.9, **params) -> DueQuery:
    params.setdefault("fallback_query", "(claude OR anthropic)")
    return DueQuery(
        id=uuid.uuid4(),
        planned=PlannedQuery(
            source_key=source_key,
            normalized_query="claude launch",
            params=params,
            query_hash=uuid.uuid4().hex,
            cadence_minutes=360,
        ),
        last_run_at=None,
        weight=weight,
    )


def poller(redis, prefix, sources, *, daily_credits=300, news=None) -> NewsPoller:
    news = news or NewsSettings()
    return NewsPoller(
        sources={s.key: s for s in sources},
        normalizer=normalizer(),
        breaker=SourceBreaker(redis, prefix, news),
        budget=QuotaCounter(redis, prefix=prefix, project="test"),
        locks=LockManager(redis, prefix=prefix),
        news=news,
        tavily=TavilySettings(news_daily_max_credits=daily_credits),
    )


LAUNCH = RawNewsItem(
    url="https://techcrunch.com/fable",
    title="Anthropic releases Claude Fable 5.2",
    snippet="",
    published_at=NOW,
)
UNDATED_SOCIAL = RawNewsItem(
    url="https://www.instagram.com/p/x", title="dengue alert", snippet="", published_at=None
)


async def test_successful_poll_normalises_drops_undated_and_schedules_next_run(redis, redis_prefix):
    source = FakeSource("tavily_news", [ok(LAUNCH, UNDATED_SOCIAL)])
    result = await poller(redis, redis_prefix, [source]).poll([due()], NOW)
    assert [item.title for item in result.items] == ["Anthropic releases Claude Fable 5.2"]
    run = result.runs[0]
    assert run.status == "ok" and run.next_run_at == NOW + timedelta(minutes=360)
    assert result.credits == 1.0


async def test_rate_limits_trip_the_breaker_and_fall_back_to_gdelt(redis, redis_prefix):
    tavily = FakeSource("tavily_news", [NewsFetchResult(items=[], cost=0, status="rate_limited")])
    gdelt = FakeSource("gdelt", [ok(LAUNCH, cost=0)])
    news = NewsSettings(breaker_failure_threshold=2)
    subject = poller(redis, redis_prefix, [tavily, gdelt], news=news)
    first = await subject.poll([due(), due()], NOW)
    assert first.runs[0].next_run_at == NOW + timedelta(minutes=news.rate_limited_retry_minutes)
    third = await subject.poll([due()], NOW)
    assert third.degraded and third.runs[0].status == "degraded"
    assert gdelt.calls[0].normalized_query == "(claude OR anthropic)"
    assert len(tavily.calls) == 2


async def test_exhausted_credits_open_the_breaker_immediately(redis, redis_prefix):
    tavily = FakeSource("tavily_news", [NewsFetchResult(items=[], cost=0, status="exhausted")])
    subject = poller(redis, redis_prefix, [tavily])
    await subject.poll([due()], NOW)
    skipped = await subject.poll([due()], NOW)
    assert skipped.skipped == {"breaker_open": 1} and len(tavily.calls) == 1


async def test_daily_tavily_budget_defers_lower_weight_queries_first(redis, redis_prefix):
    tavily = FakeSource("tavily_news", [ok(LAUNCH)])
    high, low = due(weight=1.0), due(weight=0.6)
    result = await poller(redis, redis_prefix, [tavily], daily_credits=1).poll([low, high], NOW)
    assert [c.query_hash for c in tavily.calls] == [high.planned.query_hash]
    assert result.skipped == {"tavily_budget": 1}


async def test_a_held_lock_means_another_worker_is_already_fetching(redis, redis_prefix):
    tavily = FakeSource("tavily_news", [ok(LAUNCH)])
    query = due()
    locks = LockManager(redis, prefix=redis_prefix)
    lease = await locks.try_acquire(f"news:lock:{query.planned.query_hash}", 60)
    result = await poller(redis, redis_prefix, [tavily]).poll([query], NOW)
    await locks.release(lease)
    assert result.skipped == {"locked": 1} and tavily.calls == []


async def test_primary_feed_items_may_be_undated(redis, redis_prefix):
    feed = FakeSource(
        "rss",
        [ok(RawNewsItem(url="https://openai.com/news/x", title="New model", snippet="", published_at=None))],
    )
    query = due("rss", url="https://openai.com/news/rss.xml", tier="tier1_primary")
    result = await poller(redis, redis_prefix, [feed]).poll([query], NOW)
    assert result.items[0].published_precision == "none"
    assert result.items[0].source_tier.value == "tier1_primary"


async def test_queries_fetch_side_by_side_within_the_concurrency_bound(redis, redis_prefix):
    import asyncio

    class SlowSource(FakeSource):
        def __init__(self) -> None:
            super().__init__("tavily_news", [ok(LAUNCH)])
            self.in_flight = 0
            self.peak = 0

        async def fetch(self, query, since):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return await super().fetch(query, since)

    source = SlowSource()
    news = NewsSettings(poll_concurrency=3)
    result = await poller(redis, redis_prefix, [source], news=news).poll([due() for _ in range(7)], NOW)
    assert len(source.calls) == 7 and len(result.runs) == 7
    assert source.peak == 3
