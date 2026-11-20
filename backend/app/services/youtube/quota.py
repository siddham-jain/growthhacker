import math
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.clock import Clock
from app.core.config import QuotaSettings
from app.core.logging import get_logger
from app.core.redis import QuotaCounter, ReservationStatus
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.ports.quota import QuotaExhausted, QuotaReservation
from app.ports.youtube import RequestContext
from app.services.youtube.context import as_youtube_context

logger = get_logger(__name__)

REDIS_DOWN = (RedisConnectionError, RedisTimeoutError, OSError)
RUN_BUDGET_TTL_S = 6 * 3600


class PacificDay:
    """youtube quotas reset at midnight pacific time; zoneinfo handles the dst shifts."""

    def __init__(self, timezone: str = "America/Los_Angeles") -> None:
        self.tz = ZoneInfo(timezone)

    def day(self, now: datetime) -> date:
        return now.astimezone(self.tz).date()

    def resets_at(self, now: datetime) -> datetime:
        tomorrow = self.day(now) + timedelta(days=1)
        return datetime.combine(tomorrow, time(0), tzinfo=self.tz).astimezone(UTC)


class YouTubeQuotaReservation(QuotaReservation):
    pt_day: date
    pool_bucket: str | None = None
    run_counted: bool = False
    local: bool = False


class QuotaManager:
    """atomic redis reservations per pacific day with ceilings, caps, search pools and run budgets."""

    def __init__(
        self,
        counter: QuotaCounter,
        redis: Redis,
        settings: QuotaSettings,
        clock: Clock,
        *,
        prefix: str = "gh",
    ) -> None:
        self._counter = counter
        self._redis = redis
        self._settings = settings
        self._clock = clock
        self._pacific = PacificDay(settings.timezone)
        self._prefix = prefix
        self._local_units = 0

    def pt_day(self) -> date:
        return self._pacific.day(self._clock.now())

    def resets_at(self) -> datetime:
        return self._pacific.resets_at(self._clock.now())

    def daily_limit(self, bucket: QuotaBucket) -> int:
        limits = {
            QuotaBucket.main: self._settings.main_daily_units,
            QuotaBucket.search: self._settings.search_daily_calls,
            QuotaBucket.batch_stats: self._settings.batch_stats_daily_units,
            QuotaBucket.analytics: self._settings.analytics_daily_requests,
        }
        limit = limits[bucket]
        return limit if limit is not None else 10**12

    def class_limit(self, bucket: QuotaBucket, priority: QuotaPriority) -> int:
        ceiling = self._settings.class_ceilings.get(priority.value, {}).get(bucket.value, 1.0)
        return math.floor(self.daily_limit(bucket) * ceiling)

    def client_cap(self, bucket: QuotaBucket) -> int:
        caps = {
            QuotaBucket.main: self._settings.client_daily_units,
            QuotaBucket.search: self._settings.client_daily_search_calls,
        }
        return caps.get(bucket, 0)

    def _pool_bucket(self, ctx: RequestContext) -> tuple[str, int]:
        pool = as_youtube_context(ctx).pool
        return f"search_pool_{pool.value}", getattr(self._settings.search_split, pool.value)

    def _run_key(self, run_id: UUID, bucket: QuotaBucket) -> str:
        return f"{self._prefix}:quota:{self._settings.project}:run:{run_id}:{bucket.value}"

    def _stage_key(self, run_id: UUID, bucket: QuotaBucket, stage: str) -> str:
        return f"{self._run_key(run_id, bucket)}:stage:{stage}"

    async def open_run_budget(
        self, run_id: UUID, caps: dict[QuotaBucket, int], ttl_s: int = RUN_BUDGET_TTL_S
    ) -> None:
        # the first opener fixes the caps; the counter covers every stage and attempt of the run
        async with self._redis.pipeline(transaction=True) as pipe:
            for bucket, cap in caps.items():
                pipe.set(f"{self._run_key(run_id, bucket)}:cap", cap, ex=ttl_s, nx=True)
                pipe.set(self._run_key(run_id, bucket), 0, ex=ttl_s, nx=True)
            await pipe.execute()

    async def run_usage(self, run_id: UUID, stage: str | None = None) -> dict[QuotaBucket, int]:
        keys = [
            self._run_key(run_id, bucket) if stage is None else self._stage_key(run_id, bucket, stage)
            for bucket in QuotaBucket
        ]
        values = await self._redis.mget(keys)
        return {bucket: int(value or 0) for bucket, value in zip(QuotaBucket, values, strict=True)}

    async def _take_run_budget(self, bucket: QuotaBucket, units: int, ctx: RequestContext) -> bool:
        if ctx.run_id is None:
            return False
        key = self._run_key(ctx.run_id, bucket)
        cap = await self._redis.get(f"{key}:cap")
        if cap is None:
            return False
        used = await self._redis.incrby(key, units)
        if used > int(cap):
            await self._redis.decrby(key, units)
            raise QuotaExhausted(bucket, self.resets_at())
        stage = as_youtube_context(ctx).budget_stage
        if stage is not None:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incrby(self._stage_key(ctx.run_id, bucket, stage), units)
                pipe.expire(self._stage_key(ctx.run_id, bucket, stage), RUN_BUDGET_TTL_S)
                await pipe.execute()
        return True

    async def _release_run_budget(self, bucket: QuotaBucket, units: int, ctx: RequestContext) -> None:
        if ctx.run_id is None:
            return
        await self._redis.decrby(self._run_key(ctx.run_id, bucket), units)
        stage = as_youtube_context(ctx).budget_stage
        if stage is not None:
            await self._redis.decrby(self._stage_key(ctx.run_id, bucket, stage), units)

    async def reserve(
        self, bucket: QuotaBucket, units: int, ctx: RequestContext, endpoint: str
    ) -> YouTubeQuotaReservation:
        day = self.pt_day()
        try:
            return await self._reserve(bucket, units, ctx, endpoint, day)
        except REDIS_DOWN as error:
            return self._reserve_without_redis(bucket, units, ctx, endpoint, day, error)

    async def _reserve(
        self, bucket: QuotaBucket, units: int, ctx: RequestContext, endpoint: str, day: date
    ) -> YouTubeQuotaReservation:
        run_counted = await self._take_run_budget(bucket, units, ctx)
        pool_bucket = None
        client_id = str(ctx.client_id) if ctx.client_id else None
        try:
            if bucket is QuotaBucket.search:
                pool_bucket, pool_limit = self._pool_bucket(ctx)
                pool = await self._counter.reserve(pool_bucket, day, units, limit=pool_limit)
                if not pool.ok:
                    pool_bucket = None
                    raise QuotaExhausted(bucket, self.resets_at())
            result = await self._counter.reserve(
                bucket.value,
                day,
                units,
                limit=self.class_limit(bucket, ctx.priority),
                client_id=client_id,
                client_cap=self.client_cap(bucket),
            )
            if not result.ok:
                if result.status is ReservationStatus.over_client_cap:
                    logger.info("quota_client_cap", bucket=bucket.value, client_id=client_id)
                raise QuotaExhausted(bucket, self.resets_at())
        except QuotaExhausted:
            if pool_bucket is not None:
                await self._counter.refund(pool_bucket, day, units)
            if run_counted:
                await self._release_run_budget(bucket, units, ctx)
            raise
        return YouTubeQuotaReservation(
            bucket=bucket,
            units=units,
            used_after=result.used,
            endpoint=endpoint,
            pt_day=day,
            pool_bucket=pool_bucket,
            run_counted=run_counted,
        )

    def _reserve_without_redis(
        self, bucket: QuotaBucket, units: int, ctx: RequestContext, endpoint: str, day: date, error: Exception
    ) -> YouTubeQuotaReservation:
        # fail closed, except a small conservative allowance for interactive onboarding
        allowance = self._settings.redis_down_interactive_units
        if ctx.priority is not QuotaPriority.interactive or self._local_units + units > allowance:
            logger.warning("quota_redis_unavailable", bucket=bucket.value, error=type(error).__name__)
            raise QuotaExhausted(bucket, self._clock.now() + timedelta(minutes=5)) from error
        self._local_units += units
        return YouTubeQuotaReservation(
            bucket=bucket,
            units=units,
            used_after=self._local_units,
            endpoint=endpoint,
            pt_day=day,
            local=True,
        )

    async def refund(self, reservation: QuotaReservation, ctx: RequestContext) -> None:
        if not isinstance(reservation, YouTubeQuotaReservation):
            raise TypeError("refund needs a reservation issued by this manager")
        if reservation.local:
            self._local_units -= reservation.units
            return
        client_id = str(ctx.client_id) if ctx.client_id else None
        await self._counter.refund(
            reservation.bucket.value, reservation.pt_day, reservation.units, client_id=client_id
        )
        if reservation.pool_bucket:
            await self._counter.refund(reservation.pool_bucket, reservation.pt_day, reservation.units)
        if reservation.run_counted:
            await self._release_run_budget(reservation.bucket, reservation.units, ctx)

    async def mark_exhausted(self, bucket: QuotaBucket) -> datetime:
        day = self.pt_day()
        used = await self._counter.used(bucket.value, day)
        if used < self.daily_limit(bucket):
            logger.warning("quota_drift", bucket=bucket.value, our_count=used, limit=self.daily_limit(bucket))
        await self._counter.mark_exhausted(bucket.value, day)
        return self.resets_at()

    async def used(self, bucket: QuotaBucket, client_id: UUID | None = None) -> int:
        return await self._counter.used(
            bucket.value, self.pt_day(), client_id=str(client_id) if client_id else None
        )

    async def remaining(self, bucket: QuotaBucket) -> int:
        return max(0, self.daily_limit(bucket) - await self.used(bucket))
