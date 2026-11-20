import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import LLMBudgetSettings
from app.core.logging import get_logger
from app.domain.runs import RunKind
from app.ports.llm import LLMBudgetExceeded

logger = get_logger(__name__)
MICROS = 1_000_000
COUNTER_TTL_S = 48 * 3600
CLIENT_ALERT_SHARE = 0.8
OUTAGE_CAP_SHARE = 0.5


def run_cap_usd(kind: RunKind, budgets: LLMBudgetSettings) -> float:
    caps = {
        RunKind.onboarding: budgets.onboarding_run_max_usd,
        RunKind.recrawl: budgets.onboarding_run_max_usd,
        RunKind.analysis: budgets.analysis_run_max_usd,
        RunKind.backtest: budgets.analysis_run_max_usd,
        RunKind.daily_refresh: budgets.daily_refresh_run_max_usd,
        RunKind.cards: budgets.daily_refresh_run_max_usd,
        RunKind.news_scan: budgets.daily_refresh_run_max_usd,
    }
    return caps[kind]


@dataclass(frozen=True, slots=True)
class BudgetScope:
    run_id: UUID | None
    client_id: UUID | None
    day: str

    @classmethod
    def at(cls, now: datetime, run_id: UUID | None, client_id: UUID | None) -> "BudgetScope":
        return cls(run_id=run_id, client_id=client_id, day=now.strftime("%Y%m%d"))


@dataclass(frozen=True, slots=True)
class Reservation:
    scope: BudgetScope
    micros: int


class LLMBudgetLedger(Protocol):
    async def open_run(self, run_id: UUID, cap_usd: float) -> None: ...

    # raises LLMBudgetExceeded when the call, run, client-day or global-day cap would be exceeded
    async def reserve(self, scope: BudgetScope, amount_usd: float) -> Reservation: ...

    async def settle(self, reservation: Reservation, actual_usd: float) -> None: ...


def _to_micros(amount_usd: float) -> int:
    return max(0, round(amount_usd * MICROS))


class MemoryBudgetLedger:
    def __init__(self, budgets: LLMBudgetSettings, *, cap_share: float = 1.0) -> None:
        self._budgets = budgets
        self._share = cap_share
        self._run_caps: dict[UUID, int] = {}
        self._used: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _cap(self, usd: float) -> int:
        return _to_micros(usd * self._share)

    async def open_run(self, run_id: UUID, cap_usd: float) -> None:
        self._run_caps[run_id] = self._cap(cap_usd)

    async def reserve(self, scope: BudgetScope, amount_usd: float) -> Reservation:
        micros = _to_micros(amount_usd)
        if micros > self._cap(self._budgets.per_call_max_usd):
            raise LLMBudgetExceeded(f"call estimate ${amount_usd:.4f} exceeds the per-call cap")
        checks = [(f"global:{scope.day}", self._cap(self._budgets.global_daily_max_usd), "global daily")]
        if scope.client_id is not None:
            checks.append(
                (
                    f"client:{scope.client_id}:{scope.day}",
                    self._cap(self._budgets.client_daily_max_usd),
                    "client daily",
                )
            )
        if scope.run_id is not None:
            run_cap = self._run_caps.get(scope.run_id, self._cap(self._budgets.analysis_run_max_usd))
            checks.append((f"run:{scope.run_id}", run_cap, "run"))
        async with self._lock:
            for key, cap, label in checks:
                if self._used.get(key, 0) + micros > cap:
                    raise LLMBudgetExceeded(f"{label} llm budget exhausted")
            for key, _, _ in checks:
                self._used[key] = self._used.get(key, 0) + micros
        return Reservation(scope=scope, micros=micros)

    async def settle(self, reservation: Reservation, actual_usd: float) -> None:
        delta = _to_micros(actual_usd) - reservation.micros
        scope = reservation.scope
        keys = [f"global:{scope.day}"]
        if scope.client_id is not None:
            keys.append(f"client:{scope.client_id}:{scope.day}")
        if scope.run_id is not None:
            keys.append(f"run:{scope.run_id}")
        async with self._lock:
            for key in keys:
                self._used[key] = max(0, self._used.get(key, 0) + delta)

    def used_usd(self, key: str) -> float:
        return self._used.get(key, 0) / MICROS


RESERVE_SCRIPT = """
local amount = tonumber(ARGV[1])
local ttl = tonumber(ARGV[5])
local has_run = ARGV[6] == '1'
local has_client = ARGV[7] == '1'
if has_run then
  local cap = tonumber(redis.call('GET', KEYS[2]) or ARGV[2])
  local used = tonumber(redis.call('GET', KEYS[1]) or '0')
  if used + amount > cap then return {1, used} end
end
local client_used = 0
if has_client then
  client_used = tonumber(redis.call('GET', KEYS[3]) or '0')
  if client_used + amount > tonumber(ARGV[3]) then return {2, client_used} end
end
local global_used = tonumber(redis.call('GET', KEYS[4]) or '0')
if global_used + amount > tonumber(ARGV[4]) then return {3, global_used} end
if has_run then
  redis.call('INCRBY', KEYS[1], amount)
  redis.call('EXPIRE', KEYS[1], ttl)
end
if has_client then
  client_used = redis.call('INCRBY', KEYS[3], amount)
  redis.call('EXPIRE', KEYS[3], ttl)
end
redis.call('INCRBY', KEYS[4], amount)
redis.call('EXPIRE', KEYS[4], ttl)
return {0, client_used}
"""
EXHAUSTED = {1: "run", 2: "client daily", 3: "global daily"}


class RedisBudgetLedger:
    """atomic reserve/settle across workers; while redis is down a process-local ledger runs at half caps."""

    def __init__(self, redis: Redis, prefix: str, budgets: LLMBudgetSettings) -> None:
        self._redis = redis
        self._prefix = prefix
        self._budgets = budgets
        self._reserve = redis.register_script(RESERVE_SCRIPT)
        self._fallback = MemoryBudgetLedger(budgets, cap_share=OUTAGE_CAP_SHARE)

    def _keys(self, scope: BudgetScope) -> list[str]:
        base = f"{self._prefix}:llm:budget"
        return [
            f"{base}:run:{scope.run_id}",
            f"{base}:runcap:{scope.run_id}",
            f"{base}:client:{scope.client_id}:{scope.day}",
            f"{base}:global:{scope.day}",
        ]

    async def open_run(self, run_id: UUID, cap_usd: float) -> None:
        await self._fallback.open_run(run_id, cap_usd)
        try:
            await self._redis.set(
                f"{self._prefix}:llm:budget:runcap:{run_id}", _to_micros(cap_usd), ex=COUNTER_TTL_S
            )
        except RedisError:
            logger.warning("llm_budget_store_unavailable", operation="open_run")

    async def reserve(self, scope: BudgetScope, amount_usd: float) -> Reservation:
        micros = _to_micros(amount_usd)
        budgets = self._budgets
        if micros > _to_micros(budgets.per_call_max_usd):
            raise LLMBudgetExceeded(f"call estimate ${amount_usd:.4f} exceeds the per-call cap")
        try:
            status, client_used = await self._reserve(
                keys=self._keys(scope),
                args=[
                    micros,
                    _to_micros(budgets.analysis_run_max_usd),
                    _to_micros(budgets.client_daily_max_usd),
                    _to_micros(budgets.global_daily_max_usd),
                    COUNTER_TTL_S,
                    "1" if scope.run_id is not None else "0",
                    "1" if scope.client_id is not None else "0",
                ],
            )
        except RedisError:
            logger.warning("llm_budget_store_unavailable", operation="reserve")
            return await self._fallback.reserve(scope, amount_usd)
        if int(status) != 0:
            raise LLMBudgetExceeded(f"{EXHAUSTED[int(status)]} llm budget exhausted")
        if scope.client_id is not None and int(client_used) >= CLIENT_ALERT_SHARE * _to_micros(
            budgets.client_daily_max_usd
        ):
            logger.warning("llm_client_budget_high", client_id=str(scope.client_id))
        return Reservation(scope=scope, micros=micros)

    async def settle(self, reservation: Reservation, actual_usd: float) -> None:
        delta = _to_micros(actual_usd) - reservation.micros
        if delta == 0:
            return
        scope = reservation.scope
        run_key, _, client_key, global_key = self._keys(scope)
        keys = [global_key]
        if scope.client_id is not None:
            keys.append(client_key)
        if scope.run_id is not None:
            keys.append(run_key)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                for key in keys:
                    pipe.incrby(key, delta)
                await pipe.execute()
        except RedisError:
            logger.warning("llm_budget_store_unavailable", operation="settle")
            await self._fallback.settle(reservation, actual_usd)
