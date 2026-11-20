import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import LLMResilienceSettings
from app.core.logging import get_logger

logger = get_logger(__name__)
HARD_OPEN_TTL_S = 7 * 24 * 3600


@dataclass(slots=True)
class CircuitState:
    consecutive_failures: int = 0
    outcomes: list[tuple[float, bool]] = field(default_factory=list)
    open_until: float = 0.0
    cooldown_s: float = 0.0
    probe_started_at: float = 0.0
    hard_open_hash: str | None = None


class CircuitStore(Protocol):
    async def load(self, key: str) -> CircuitState | None: ...

    async def save(self, key: str, state: CircuitState, ttl_s: float) -> None: ...

    async def reset(self, key: str) -> None: ...


class MemoryCircuitStore:
    def __init__(self) -> None:
        self._states: dict[str, CircuitState] = {}

    async def load(self, key: str) -> CircuitState | None:
        return self._states.get(key)

    async def save(self, key: str, state: CircuitState, ttl_s: float) -> None:
        self._states[key] = state

    async def reset(self, key: str) -> None:
        self._states.pop(key, None)


class RedisCircuitStore:
    # shared by every worker; falls back to process memory while redis is unreachable
    def __init__(self, redis: Redis, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix
        self._fallback = MemoryCircuitStore()

    def _key(self, key: str) -> str:
        return f"{self._prefix}:llm:cb:{key}"

    async def load(self, key: str) -> CircuitState | None:
        try:
            raw = await self._redis.get(self._key(key))
        except RedisError:
            logger.warning("llm_circuit_store_unavailable", operation="load")
            return await self._fallback.load(key)
        if raw is None:
            return None
        data = json.loads(raw)
        data["outcomes"] = [tuple(item) for item in data.get("outcomes", [])]
        return CircuitState(**data)

    async def save(self, key: str, state: CircuitState, ttl_s: float) -> None:
        try:
            await self._redis.set(self._key(key), json.dumps(asdict(state)), ex=max(1, int(ttl_s)))
        except RedisError:
            logger.warning("llm_circuit_store_unavailable", operation="save")
            await self._fallback.save(key, state, ttl_s)

    async def reset(self, key: str) -> None:
        try:
            await self._redis.delete(self._key(key))
        except RedisError:
            await self._fallback.reset(key)


def provider_key(provider: str) -> str:
    return f"{provider}:*"


class CircuitBreaker:
    """closed -> open after repeated failures; half-open admits one probe; hard-open waits for a config change."""

    def __init__(
        self,
        settings: LLMResilienceSettings,
        store: CircuitStore,
        *,
        config_hash: str,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._store = store
        self._config_hash = config_hash
        self._now = now

    def _ttl(self, state: CircuitState) -> float:
        if state.hard_open_hash is not None:
            return HARD_OPEN_TTL_S
        return max(self._settings.circuit_window_s, state.open_until - self._now()) + state.cooldown_s + 60

    async def _admits(self, key: str) -> bool:
        state = await self._store.load(key)
        if state is None:
            return True
        if state.hard_open_hash is not None:
            return state.hard_open_hash != self._config_hash
        now = self._now()
        if state.open_until == 0.0:
            return True
        if now < state.open_until:
            return False
        if state.probe_started_at >= state.open_until and now - state.probe_started_at < state.cooldown_s:
            return False
        state.probe_started_at = now
        await self._store.save(key, state, self._ttl(state))
        return True

    async def allow(self, route_key: str, provider: str) -> bool:
        return await self._admits(provider_key(provider)) and await self._admits(route_key)

    async def record_success(self, route_key: str) -> None:
        state = await self._store.load(route_key) or CircuitState()
        if state.hard_open_hash is not None:
            return
        now = self._now()
        state.consecutive_failures = 0
        state.open_until = 0.0
        state.cooldown_s = 0.0
        state.probe_started_at = 0.0
        state.outcomes = self._recent(state, now) + [(now, True)]
        await self._store.save(route_key, state, self._ttl(state))

    def _recent(self, state: CircuitState, now: float) -> list[tuple[float, bool]]:
        window = self._settings.circuit_window_s
        recent = [item for item in state.outcomes if now - item[0] <= window]
        return recent[-(self._settings.circuit_window_calls - 1) :]

    async def record_failure(self, route_key: str) -> None:
        settings = self._settings
        state = await self._store.load(route_key) or CircuitState()
        if state.hard_open_hash is not None:
            return
        now = self._now()
        state.consecutive_failures += 1
        state.outcomes = self._recent(state, now) + [(now, False)]
        failures = sum(1 for _, ok in state.outcomes if not ok)
        ratio_tripped = (
            len(state.outcomes) >= settings.circuit_window_calls // 2
            and failures / len(state.outcomes) >= settings.circuit_failure_ratio
        )
        probing = state.open_until > 0.0 and now >= state.open_until
        if probing or state.consecutive_failures >= settings.circuit_consecutive_failures or ratio_tripped:
            previous = state.cooldown_s
            state.cooldown_s = (
                min(settings.circuit_max_cooldown_s, previous * 2)
                if previous
                else settings.circuit_cooldown_s
            )
            state.open_until = now + state.cooldown_s
            logger.warning("llm_circuit_opened", route=route_key, cooldown_s=state.cooldown_s)
        await self._store.save(route_key, state, self._ttl(state))

    async def open_for(self, key: str, seconds: float) -> None:
        state = await self._store.load(key) or CircuitState()
        state.cooldown_s = seconds
        state.open_until = self._now() + seconds
        logger.warning("llm_circuit_opened", route=key, cooldown_s=seconds)
        await self._store.save(key, state, self._ttl(state))

    async def hard_open(self, key: str) -> None:
        state = CircuitState(hard_open_hash=self._config_hash)
        logger.error("llm_circuit_hard_open", route=key)
        await self._store.save(key, state, HARD_OPEN_TTL_S)

    async def reset(self, key: str) -> None:
        await self._store.reset(key)
