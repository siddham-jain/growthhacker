import asyncio
import random
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from app.adapters.youtube.costs import cost_of
from app.adapters.youtube.errors import (
    CircuitOpen,
    GoogleQuotaExceeded,
    KeyRejected,
    NotSent,
    Transient,
    YouTubeApiError,
)
from app.adapters.youtube.http import ApiRequest, ApiResponse, RequestHandler
from app.core.clock import Clock
from app.core.config import YouTubeRetrySettings
from app.core.logging import get_logger
from app.core.redis import RateLimiter
from app.domain.youtube import QuotaBucket, QuotaUsage
from app.ports.quota import QuotaExhausted, QuotaReservation
from app.ports.youtube import RequestContext

logger = get_logger(__name__)


class QuotaPort(Protocol):
    async def reserve(
        self, bucket: QuotaBucket, units: int, ctx: RequestContext, endpoint: str
    ) -> QuotaReservation: ...

    async def refund(self, reservation: QuotaReservation, ctx: RequestContext) -> None: ...

    async def mark_exhausted(self, bucket: QuotaBucket) -> datetime: ...


class UsageSink(Protocol):
    async def record(self, usage: QuotaUsage, *, cache_key: str | None = None) -> None: ...


class ThrottledHandler:
    def __init__(
        self,
        inner: RequestHandler,
        *,
        max_concurrency: int,
        rate_limiter: RateLimiter | None = None,
        requests_per_s: float = 20,
    ) -> None:
        self._inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._rate_limiter = rate_limiter
        self._rate = requests_per_s

    async def send(self, request: ApiRequest) -> ApiResponse:
        async with self._semaphore:
            if self._rate_limiter is not None:
                await self._rate_limiter.take("youtube", rate_per_s=self._rate, capacity=self._rate)
            return await self._inner.send(request)


class MeteredHandler:
    """reserves units before every attempt; refunds only requests that never left the process."""

    def __init__(self, inner: RequestHandler, quota: QuotaPort, ledger: UsageSink | None = None) -> None:
        self._inner = inner
        self._quota = quota
        self._ledger = ledger

    async def send(self, request: ApiRequest) -> ApiResponse:
        cost = cost_of(request.endpoint)
        reservation = await self._quota.reserve(cost.bucket, cost.units, request.ctx, request.endpoint)
        try:
            response = await self._inner.send(request)
        except NotSent:
            await self._quota.refund(reservation, request.ctx)
            raise
        except GoogleQuotaExceeded as error:
            await self._record(request, cost.bucket, cost.units, error.status or 403, error.reason)
            resets_at = await self._quota.mark_exhausted(cost.bucket)
            raise QuotaExhausted(cost.bucket, resets_at, "google") from error
        except YouTubeApiError as error:
            await self._record(request, cost.bucket, cost.units, error.status or 0, error.reason)
            raise
        await self._record(request, cost.bucket, cost.units, response.status, None)
        return response

    async def _record(
        self, request: ApiRequest, bucket: QuotaBucket, units: int, status: int, reason: str | None
    ) -> None:
        if self._ledger is None:
            return
        ctx = request.ctx
        usage = QuotaUsage(
            bucket=bucket,
            endpoint=request.endpoint,
            units=units,
            client_id=ctx.client_id,
            run_id=ctx.run_id,
            priority=ctx.priority,
            scope="client" if ctx.client_id else "shared",
            cache_outcome=request.cache_outcome,
            http_status=status,
            reason=reason,
        )
        await self._ledger.record(usage)


class CircuitBreakerHandler:
    """per-endpoint breaker for 5xx bursts, plus a key-wide breaker when google rejects the api key."""

    def __init__(self, inner: RequestHandler, clock: Clock, settings: YouTubeRetrySettings) -> None:
        self._inner = inner
        self._clock = clock
        self._settings = settings
        self._failures: dict[str, deque[float]] = {}
        self._open_until: dict[str, float] = {}

    def _now(self) -> float:
        return self._clock.now().timestamp()

    def _check(self, name: str, error: type[YouTubeApiError]) -> None:
        until = self._open_until.get(name)
        if until is not None and self._now() < until:
            raise error(f"circuit open for {name}", reason="circuitOpen")

    async def send(self, request: ApiRequest) -> ApiResponse:
        uses_key = request.bearer is None
        if uses_key:
            self._check("api_key", KeyRejected)
        self._check(request.endpoint, CircuitOpen)
        try:
            response = await self._inner.send(request)
        except KeyRejected:
            if uses_key:
                self._open_until["api_key"] = self._now() + self._settings.key_circuit_open_s
                logger.error("youtube_key_rejected", endpoint=request.endpoint)
            raise
        except Transient as error:
            if error.status is not None and error.status >= 500:
                self._record_failure(request.endpoint)
            raise
        self._failures.pop(request.endpoint, None)
        return response

    def _record_failure(self, endpoint: str) -> None:
        now = self._now()
        window = self._failures.setdefault(endpoint, deque())
        window.append(now)
        while window and now - window[0] > self._settings.circuit_window_s:
            window.popleft()
        if len(window) >= self._settings.circuit_failures:
            self._open_until[endpoint] = now + self._settings.circuit_open_s
            window.clear()
            logger.warning("youtube_circuit_open", endpoint=endpoint)


class RetryingHandler:
    """exponential backoff with full jitter, honouring retry-after; each attempt is metered below."""

    def __init__(
        self,
        inner: RequestHandler,
        settings: YouTubeRetrySettings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()

    def delay(self, attempt: int, retry_after_s: float | None) -> float:
        if retry_after_s is not None:
            return min(self._settings.cap_s, retry_after_s)
        return self._rng.uniform(0, min(self._settings.cap_s, self._settings.base_s * 2**attempt))

    async def send(self, request: ApiRequest) -> ApiResponse:
        attempt = 0
        while True:
            try:
                return await self._inner.send(request)
            except (Transient, NotSent) as error:
                attempt += 1
                if attempt >= self._settings.max_attempts:
                    raise
                retry_after = error.retry_after_s if isinstance(error, Transient) else None
                await self._sleep(self.delay(attempt, retry_after))


def build_handler_chain(
    http: RequestHandler,
    *,
    quota: QuotaPort,
    ledger: UsageSink | None,
    clock: Clock,
    settings: YouTubeRetrySettings,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RequestHandler:
    throttled = ThrottledHandler(
        http,
        max_concurrency=settings.max_concurrency,
        rate_limiter=rate_limiter,
        requests_per_s=settings.requests_per_s,
    )
    metered = MeteredHandler(throttled, quota, ledger)
    guarded = CircuitBreakerHandler(metered, clock, settings)
    return RetryingHandler(guarded, settings, sleep=sleep)
