import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from uuid import UUID


class AdaptiveLimiter:
    """concurrency cap with aimd: halve on 429 for a cooldown, then +1 per n successes."""

    def __init__(
        self,
        limit: int,
        *,
        cooldown_s: float = 60,
        recovery_successes: int = 10,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limiter limit must be at least 1")
        self.limit = limit
        self.effective = limit
        self._cooldown_s = cooldown_s
        self._recovery_successes = recovery_successes
        self._now = now
        self._in_use = 0
        self._successes = 0
        self._cooldown_until = 0.0
        self._condition = asyncio.Condition()

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._condition:
            await self._condition.wait_for(lambda: self._in_use < self.effective)
            self._in_use += 1
        try:
            yield
        finally:
            async with self._condition:
                self._in_use -= 1
                self._condition.notify_all()

    @property
    def in_use(self) -> int:
        return self._in_use

    def on_rate_limited(self) -> None:
        self.effective = max(1, self.effective // 2)
        self._successes = 0
        self._cooldown_until = self._now() + self._cooldown_s

    async def on_success(self) -> None:
        if self.effective >= self.limit or self._now() < self._cooldown_until:
            return
        self._successes += 1
        if self._successes >= self._recovery_successes:
            self._successes = 0
            async with self._condition:
                self.effective += 1
                self._condition.notify_all()


class RunLimiter:
    """per-run semaphores so one client's run cannot starve the others."""

    def __init__(self, per_run: int) -> None:
        self._per_run = per_run
        self._semaphores: dict[UUID, asyncio.Semaphore] = {}
        self._holders: dict[UUID, int] = {}

    @contextlib.asynccontextmanager
    async def slot(self, run_id: UUID | None) -> AsyncIterator[None]:
        if run_id is None:
            yield
            return
        semaphore = self._semaphores.setdefault(run_id, asyncio.Semaphore(self._per_run))
        self._holders[run_id] = self._holders.get(run_id, 0) + 1
        try:
            async with semaphore:
                yield
        finally:
            self._holders[run_id] -= 1
            if self._holders[run_id] == 0:
                del self._holders[run_id]
                del self._semaphores[run_id]

    def active_runs(self) -> int:
        return len(self._semaphores)
