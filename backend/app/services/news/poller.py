import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from redis.asyncio import Redis

from app.core.config import NewsSettings, TavilySettings
from app.core.redis import LockManager, QuotaCounter
from app.domain.news import NewsItem, SourceTier
from app.ports.freshness import FreshnessSource, NewsFetchResult, PlannedQuery
from app.services.news.normalize import ItemNormalizer
from app.services.news.query_planner import CREDIT_SOURCES
from app.services.news.repository import DueQuery

logger = logging.getLogger(__name__)

TAVILY_BUDGET_BUCKET = "tavily_news_credits"
FALLBACK_SOURCE = "gdelt"
FAILURE_STATUSES = frozenset({"error", "rate_limited"})
LOOKBACK = timedelta(days=14)
OVERLAP = timedelta(days=1)
EXHAUSTED_OPEN = timedelta(hours=24)


class SourceBreaker:
    """per-source circuit state in redis, shared by every worker."""

    def __init__(self, redis: Redis, prefix: str, settings: NewsSettings) -> None:
        self._redis = redis
        self._prefix = prefix
        self._settings = settings

    def _key(self, source_key: str, suffix: str) -> str:
        return f"{self._prefix}:news:breaker:{source_key}:{suffix}"

    async def is_open(self, source_key: str) -> bool:
        return bool(await self._redis.exists(self._key(source_key, "open")))

    async def trip(self, source_key: str, seconds: float) -> None:
        await self._redis.set(self._key(source_key, "open"), "1", ex=max(int(seconds), 1))

    async def record(self, source_key: str, status: str) -> None:
        if status == "exhausted":
            await self.trip(source_key, EXHAUSTED_OPEN.total_seconds())
            return
        failures = self._key(source_key, "failures")
        if status not in FAILURE_STATUSES:
            await self._redis.delete(failures)
            return
        count = await self._redis.incr(failures)
        await self._redis.expire(failures, self._settings.breaker_window_s)
        if count >= self._settings.breaker_failure_threshold:
            await self.trip(source_key, self._settings.breaker_open_s)
            await self._redis.delete(failures)


@dataclass(frozen=True)
class QueryRunRecord:
    query_id: object
    started_at: datetime
    finished_at: datetime
    status: str
    cost: float
    item_count: int
    error: str | None
    request_id: str | None
    next_run_at: datetime


@dataclass
class PollResult:
    items: list[NewsItem] = field(default_factory=list)
    runs: list[QueryRunRecord] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    degraded: bool = False
    credits: float = 0.0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class NewsPoller:
    """runs due shared queries once for all subscribers: lock, budget, breaker, fallback, normalise."""

    def __init__(
        self,
        *,
        sources: Mapping[str, FreshnessSource],
        normalizer: ItemNormalizer,
        breaker: SourceBreaker,
        budget: QuotaCounter,
        locks: LockManager,
        news: NewsSettings,
        tavily: TavilySettings,
    ) -> None:
        self._sources = sources
        self._normalizer = normalizer
        self._breaker = breaker
        self._budget = budget
        self._locks = locks
        self._news = news
        self._tavily = tavily

    def _since(self, due: DueQuery, now: datetime) -> datetime:
        if due.last_run_at is None:
            return now - LOOKBACK
        return max(due.last_run_at - OVERLAP, now - LOOKBACK)

    async def _reserve_credits(self, now: datetime) -> bool:
        reservation = await self._budget.reserve(
            TAVILY_BUDGET_BUCKET, now.date(), 1, limit=self._tavily.news_daily_max_credits
        )
        return reservation.ok

    async def _settle_credits(self, cost: float, now: datetime) -> None:
        extra = math.ceil(cost) - 1
        if extra > 0:
            await self._budget.reserve(TAVILY_BUDGET_BUCKET, now.date(), extra, limit=10**9)
        elif cost == 0:
            await self._budget.refund(TAVILY_BUDGET_BUCKET, now.date(), 1)

    def _fallback(self, planned: PlannedQuery) -> PlannedQuery | None:
        query = planned.params.get("fallback_query")
        if planned.source_key != "tavily_news" or not query or FALLBACK_SOURCE not in self._sources:
            return None
        return planned.model_copy(
            update={"source_key": FALLBACK_SOURCE, "normalized_query": query, "params": {}}
        )

    async def _fetch(
        self, due: DueQuery, now: datetime, result: PollResult
    ) -> tuple[PlannedQuery, NewsFetchResult] | None:
        planned = due.planned
        if await self._breaker.is_open(planned.source_key):
            fallback = self._fallback(planned)
            if fallback is None or await self._breaker.is_open(FALLBACK_SOURCE):
                result.skip("breaker_open")
                return None
            result.degraded = True
            planned = fallback
        source = self._sources.get(planned.source_key)
        if source is None:
            result.skip("unknown_source")
            return None
        if planned.source_key in CREDIT_SOURCES and not await self._reserve_credits(now):
            result.skip("tavily_budget")
            return None
        fetched = await source.fetch(planned, self._since(due, now))
        await self._breaker.record(planned.source_key, fetched.status)
        if planned.source_key in CREDIT_SOURCES:
            await self._settle_credits(fetched.cost, now)
            result.credits += fetched.cost
        return planned, fetched

    def _next_run(self, due: DueQuery, status: str, now: datetime) -> datetime:
        if status in {"rate_limited", "error", "exhausted"}:
            return now + timedelta(minutes=self._news.rate_limited_retry_minutes)
        return now + timedelta(minutes=max(due.planned.cadence_minutes, 1))

    def _normalise(self, planned: PlannedQuery, fetched: NewsFetchResult, now: datetime) -> list[NewsItem]:
        feed_tier = planned.params.get("tier")
        items = []
        for raw in fetched.items:
            outcome = self._normalizer.normalize(
                raw,
                source_key=planned.source_key,
                fetched_at=now,
                feed_tier=SourceTier(feed_tier) if feed_tier else None,
            )
            if outcome.item is None:
                continue
            # undated items only count when they come from the entity's own feed
            if outcome.item.published_precision == "none" and feed_tier != SourceTier.tier1_primary.value:
                continue
            items.append(outcome.item)
        return items

    async def poll(self, due: Sequence[DueQuery], now: datetime) -> PollResult:
        result = PollResult()
        ordered = sorted(due, key=lambda d: -d.weight)
        # fetches are network bound, so a client's forced poll runs them side by side
        slots = asyncio.Semaphore(self._news.poll_concurrency)

        async def bounded(query: DueQuery) -> None:
            async with slots:
                await self._poll_one(query, now, result)

        await asyncio.gather(*(bounded(query) for query in ordered))
        return result

    async def _poll_one(self, query: DueQuery, now: datetime, result: PollResult) -> None:
        lease = await self._locks.try_acquire(
            f"news:lock:{query.planned.query_hash}", self._news.query_lock_ttl_s
        )
        if lease is None:
            result.skip("locked")
            return
        try:
            started = now
            fetched_pair = await self._fetch(query, now, result)
            if fetched_pair is None:
                return
            planned, fetched = fetched_pair
            items = self._normalise(planned, fetched, now) if fetched.status in {"ok", "partial"} else []
            result.items.extend(items)
            status = "degraded" if planned.source_key != query.planned.source_key else fetched.status
            result.runs.append(
                QueryRunRecord(
                    query_id=query.id,
                    started_at=started,
                    finished_at=now,
                    status=status,
                    cost=fetched.cost,
                    item_count=len(items),
                    error=fetched.error,
                    request_id=fetched.provider_request_id,
                    next_run_at=self._next_run(query, fetched.status, now),
                )
            )
        except Exception:
            logger.exception("news query failed", extra={"source": query.planned.source_key})
            await self._breaker.record(query.planned.source_key, "error")
            result.skip("exception")
        finally:
            await self._locks.release(lease)
