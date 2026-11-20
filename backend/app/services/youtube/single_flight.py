import asyncio
import secrets
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis

RELEASE_MANY_SCRIPT = """
local released = 0
for i, key in ipairs(KEYS) do
  if redis.call('GET', key) == ARGV[1] then
    released = released + redis.call('DEL', key)
  end
end
return released
"""


@dataclass
class _Pending[R]:
    context: Any = None
    ids: list[str] = field(default_factory=list)
    futures: list[tuple[list[str], asyncio.Future[dict[str, R]]]] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None


class BatchCoalescer[R]:
    """merges concurrent lookups from one quota context into calls of at most max_batch ids.

    callers with different contexts never share a call, so each pays for its own lookups and one
    caller's quota cap cannot fail another's request.
    """

    def __init__(
        self,
        fetch: Callable[[list[str], Any], Awaitable[dict[str, R]]],
        *,
        max_batch: int = 50,
        window_s: float = 0.15,
        group_key: Callable[[Any], Hashable] = lambda context: context,
    ) -> None:
        self._fetch = fetch
        self._max_batch = max_batch
        self._window_s = window_s
        self._group_key = group_key
        self._pending: dict[Hashable, _Pending[R]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, ids: Sequence[str], context: Any = None) -> dict[str, R]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, R]] = loop.create_future()
        key = self._group_key(context)
        pending = self._pending.get(key)
        if pending is None:
            pending = self._pending[key] = _Pending(context=context)
        pending.futures.append((wanted, future))
        pending.ids.extend(i for i in wanted if i not in pending.ids)
        if len(pending.ids) >= self._max_batch:
            self._flush(key)
        elif pending.timer is None:
            pending.timer = loop.call_later(self._window_s, self._flush, key)
        return await future

    def _flush(self, key: Hashable) -> None:
        pending = self._pending.pop(key, None)
        if pending is None:
            return
        if pending.timer is not None:
            pending.timer.cancel()
        task = asyncio.get_running_loop().create_task(self._run(pending))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, pending: _Pending[R]) -> None:
        results: dict[str, R] = {}
        try:
            for start in range(0, len(pending.ids), self._max_batch):
                results.update(
                    await self._fetch(pending.ids[start : start + self._max_batch], pending.context)
                )
        except Exception as error:
            for _, future in pending.futures:
                if not future.done():
                    future.set_exception(error)
            return
        for wanted, future in pending.futures:
            if not future.done():
                future.set_result({i: results[i] for i in wanted if i in results})


class InflightClaims:
    """cross-worker claim per id; ids claimed elsewhere are awaited instead of fetched twice."""

    def __init__(self, redis: Redis, *, prefix: str = "gh", ttl_s: int = 30, poll_s: float = 0.1) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl_ms = ttl_s * 1000
        self._wait_s = ttl_s
        self._poll_s = poll_s
        self._release = redis.register_script(RELEASE_MANY_SCRIPT)

    def _key(self, kind: str, entity_id: str) -> str:
        return f"{self._prefix}:inflight:{kind}:{entity_id}"

    async def claim(self, kind: str, ids: Sequence[str]) -> tuple[str, list[str], list[str]]:
        token = secrets.token_hex(8)
        async with self._redis.pipeline(transaction=False) as pipe:
            for entity_id in ids:
                pipe.set(self._key(kind, entity_id), token, nx=True, px=self._ttl_ms)
            results = await pipe.execute()
        mine = [entity_id for entity_id, ok in zip(ids, results, strict=True) if ok]
        others = [entity_id for entity_id, ok in zip(ids, results, strict=True) if not ok]
        return token, mine, others

    async def release(self, kind: str, token: str, ids: Sequence[str]) -> None:
        if ids:
            await self._release(keys=[self._key(kind, entity_id) for entity_id in ids], args=[token])

    async def wait(self, kind: str, ids: Sequence[str]) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait_s
        remaining = list(ids)
        while remaining and loop.time() < deadline:
            async with self._redis.pipeline(transaction=False) as pipe:
                for entity_id in remaining:
                    pipe.exists(self._key(kind, entity_id))
                states = await pipe.execute()
            remaining = [entity_id for entity_id, held in zip(remaining, states, strict=True) if held]
            if remaining:
                await asyncio.sleep(self._poll_s)
