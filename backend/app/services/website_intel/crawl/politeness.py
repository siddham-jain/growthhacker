import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.core.redis import RateLimiter

MAX_RETRY_AFTER_S = 120.0
MAX_BACKOFF_DELAY_S = 10.0
STOP_AFTER_CONSECUTIVE = 3

Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class _HostState:
    concurrency: int
    delay_s: float
    semaphore: asyncio.Semaphore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_start: float | None = None
    consecutive_throttles: int = 0
    stopped: bool = False


class HostGovernor:
    """per-host concurrency, spacing between request starts and adaptive backoff (plan 03 §5.6)."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        min_delay_s: float,
        rate_limiter: RateLimiter | None = None,
        sleep: Sleeper = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._min_delay_s = min_delay_s
        self._rate_limiter = rate_limiter
        self._sleep = sleep
        self._monotonic = monotonic
        self._hosts: dict[str, _HostState] = {}

    def _state(self, host: str) -> _HostState:
        if host not in self._hosts:
            self._hosts[host] = _HostState(
                concurrency=self._max_concurrency,
                delay_s=self._min_delay_s,
                semaphore=asyncio.Semaphore(self._max_concurrency),
            )
        return self._hosts[host]

    def set_delay(self, host: str, delay_s: float) -> None:
        state = self._state(host)
        state.delay_s = max(state.delay_s, delay_s)

    def delay_for(self, host: str) -> float:
        return self._state(host).delay_s

    def stopped(self, host: str) -> bool:
        return self._state(host).stopped

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[None]:
        state = self._state(host)
        async with state.semaphore:
            async with state.lock:
                wait = (
                    0.0 if state.last_start is None else state.last_start + state.delay_s - self._monotonic()
                )
                if wait > 0:
                    await self._sleep(wait)
                if self._rate_limiter is not None:
                    # shared spacing across workers and clients that crawl the same host
                    await self._rate_limiter.take(
                        f"hostgov:{host}", rate_per_s=1.0 / max(state.delay_s, 0.01), capacity=1
                    )
                state.last_start = self._monotonic()
            yield

    def record(self, host: str, status_code: int | None, retry_after_s: float | None = None) -> float | None:
        """returns how long to wait before retrying, or None when the response needs no retry."""
        state = self._state(host)
        if status_code in {429, 503}:
            state.consecutive_throttles += 1
            if state.consecutive_throttles >= STOP_AFTER_CONSECUTIVE:
                state.stopped = True
                return None
            if status_code == 429:
                state.concurrency = max(1, state.concurrency // 2)
                state.semaphore = asyncio.Semaphore(state.concurrency)
                state.delay_s = min(MAX_BACKOFF_DELAY_S, state.delay_s * 2)
            return min(retry_after_s if retry_after_s is not None else state.delay_s, MAX_RETRY_AFTER_S)
        state.consecutive_throttles = 0
        return None
