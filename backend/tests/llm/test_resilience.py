import asyncio
import random
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.adapters.llm.capabilities import anthropic_capabilities, sized_max_tokens
from app.adapters.llm.circuit_breaker import CircuitBreaker, MemoryCircuitStore, RedisCircuitStore
from app.adapters.llm.limiter import AdaptiveLimiter, RunLimiter
from app.adapters.llm.retry_policy import RetryPolicy
from app.adapters.llm.routing import TaskRouter
from app.core.config import LLMBudgetSettings, LLMResilienceSettings
from app.domain.runs import RunKind
from app.ports.llm import LLMBudgetExceeded, LLMError, LLMErrorKind, TaskId, TokenUsage
from app.services.llm.budgets import BudgetScope, MemoryBudgetLedger, RedisBudgetLedger, run_cap_usd
from app.services.llm.costs import CostCalculator
from app.services.llm.tasks import resolve_task
from tests.llm.factories import NOW, FakeMonotonic, make_llm_settings

SETTINGS = LLMResilienceSettings()


def breaker(clock: FakeMonotonic, config_hash: str = "h1", store=None) -> CircuitBreaker:
    return CircuitBreaker(SETTINGS, store or MemoryCircuitStore(), config_hash=config_hash, now=clock)


async def test_circuit_opens_after_consecutive_failures_and_probes_after_cooldown() -> None:
    clock = FakeMonotonic()
    circuit = breaker(clock)
    for _ in range(SETTINGS.circuit_consecutive_failures):
        assert await circuit.allow("anthropic:m", "anthropic")
        await circuit.record_failure("anthropic:m")
    assert not await circuit.allow("anthropic:m", "anthropic")
    clock.now += SETTINGS.circuit_cooldown_s
    assert await circuit.allow("anthropic:m", "anthropic")
    assert not await circuit.allow("anthropic:m", "anthropic")
    await circuit.record_failure("anthropic:m")
    clock.now += SETTINGS.circuit_cooldown_s
    assert not await circuit.allow("anthropic:m", "anthropic")
    clock.now += SETTINGS.circuit_cooldown_s
    assert await circuit.allow("anthropic:m", "anthropic")
    await circuit.record_success("anthropic:m")
    assert await circuit.allow("anthropic:m", "anthropic")


async def test_circuit_opens_on_failure_ratio() -> None:
    clock = FakeMonotonic()
    circuit = breaker(clock)
    for index in range(10):
        if index % 2:
            await circuit.record_failure("r")
        else:
            await circuit.record_success("r")
    assert not await circuit.allow("r", "p")


async def test_hard_open_clears_only_on_config_change() -> None:
    clock = FakeMonotonic()
    store = MemoryCircuitStore()
    await breaker(clock, "h1", store).hard_open("anthropic:*")
    clock.now += 10_000
    assert not await breaker(clock, "h1", store).allow("anthropic:m", "anthropic")
    assert await breaker(clock, "h2", store).allow("anthropic:m", "anthropic")


async def test_redis_circuit_state_is_shared(redis: Redis, redis_prefix: str) -> None:
    clock = FakeMonotonic()
    first = breaker(clock, store=RedisCircuitStore(redis, redis_prefix))
    second = breaker(clock, store=RedisCircuitStore(redis, redis_prefix))
    await first.open_for("anthropic:*", 60)
    assert not await second.allow("anthropic:m", "anthropic")


async def test_redis_circuit_store_falls_back_to_memory() -> None:
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.1)
    store = RedisCircuitStore(dead, "x")
    circuit = breaker(FakeMonotonic(), store=store)
    await circuit.open_for("r", 60)
    assert not await circuit.allow("r", "p")
    await dead.aclose()


async def test_adaptive_limiter_halves_and_recovers() -> None:
    clock = FakeMonotonic()
    limiter = AdaptiveLimiter(8, cooldown_s=60, recovery_successes=2, now=clock)
    limiter.on_rate_limited()
    assert limiter.effective == 4
    await limiter.on_success()
    assert limiter.effective == 4
    clock.now += 61
    for _ in range(2):
        await limiter.on_success()
    assert limiter.effective == 5


async def test_limiter_caps_concurrency() -> None:
    limiter = AdaptiveLimiter(2)
    peak = 0

    async def job() -> None:
        nonlocal peak
        async with limiter.slot():
            peak = max(peak, limiter.in_use)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(job() for _ in range(6)))
    assert peak == 2


async def test_run_limiter_cleans_up_finished_runs() -> None:
    limiter = RunLimiter(2)
    run_id = uuid4()
    async with limiter.slot(run_id):
        assert limiter.active_runs() == 1
    assert limiter.active_runs() == 0


def test_retry_policy_respects_retry_after_and_deadline() -> None:
    policy = RetryPolicy(SETTINGS, jitter=random.Random(1))
    short = LLMError(LLMErrorKind.rate_limited, "x", retry_after_s=2)
    long = LLMError(LLMErrorKind.rate_limited, "x", retry_after_s=30)
    assert policy.retry_delay(short, 0, remaining_s=None, timeout_s=20) == 2
    assert policy.retry_delay(long, 0, remaining_s=None, timeout_s=20) is None
    timeout = LLMError(LLMErrorKind.timeout, "x")
    assert policy.retry_delay(timeout, 0, remaining_s=15, timeout_s=20) is None
    delay = policy.retry_delay(LLMError(LLMErrorKind.server_error, "x"), 3, remaining_s=None, timeout_s=20)
    assert delay is not None and SETTINGS.retry_cap_s <= delay <= SETTINGS.retry_cap_s + SETTINGS.retry_base_s


def test_routing_table_and_cross_tier_stand_ins() -> None:
    settings = make_llm_settings()
    router = TaskRouter(settings)
    low = router.resolve(resolve_task(TaskId.card_critique, settings))
    assert [(r.provider, r.model, r.degraded) for r in low] == [
        ("anthropic", "claude-haiku-4-5", False),
        ("openai_compat", "gw-low", False),
        ("anthropic", "claude-sonnet-5-5", False),
        ("openai_compat", "gw-medium", False),
    ]
    medium = router.resolve(resolve_task(TaskId.card_write, settings))
    assert medium[0].effort == "low" and medium[-1].degraded
    high = router.resolve(resolve_task(TaskId.website_profile_reduce, settings))
    assert high[0].effort == "medium" and high[0].max_output_tokens == 16_384
    override = make_llm_settings(tasks={"card_write": {"tier": "high", "timeout_s": 50}})
    spec = resolve_task(TaskId.card_write, override)
    assert spec.tier == "high" and spec.timeout_s == 50


def test_routes_skip_unconfigured_fallback() -> None:
    settings = make_llm_settings(openai_compat={})
    routes = TaskRouter(settings).resolve(resolve_task(TaskId.card_write, settings))
    assert {route.provider for route in routes} == {"anthropic"}


def test_capability_table_and_sizing() -> None:
    assert anthropic_capabilities("claude-haiku-4-5-20251001").effort_levels == frozenset()
    assert anthropic_capabilities("claude-sonnet-5-5").thinking == "adaptive"
    assert anthropic_capabilities("claude-unknown-9").max_output_tokens == 8192
    assert sized_max_tokens(anthropic_capabilities("claude-sonnet-5-5"), 1000, 800) == 1600
    assert sized_max_tokens(anthropic_capabilities("claude-haiku-4-5"), 1000, 800) == 1000


def test_cost_calculator() -> None:
    costs = CostCalculator(make_llm_settings().prices)
    usage = TokenUsage(input_tokens=1000, cache_write_tokens=2000, cache_read_tokens=3000, output_tokens=500)
    expected = (1000 * 2.0 + 2000 * 2.5 + 3000 * 0.10 + 500 * 10.0) / 1_000_000
    assert costs.cost("claude-sonnet-5-5", usage) == pytest.approx(expected)
    assert costs.cost("claude-haiku-4-5-20251001", TokenUsage(output_tokens=1000)) == pytest.approx(0.005)
    assert costs.cost("gw-medium", usage) is None
    assert costs.estimate("gw-medium", 1000, 1000) == pytest.approx((1000 * 2.0 + 1000 * 10.0) / 1_000_000)


def test_run_caps_per_kind() -> None:
    budgets = LLMBudgetSettings()
    assert run_cap_usd(RunKind.onboarding, budgets) == 1.50
    assert run_cap_usd(RunKind.daily_refresh, budgets) == 0.75


async def test_memory_ledger_enforces_every_cap() -> None:
    ledger = MemoryBudgetLedger(LLMBudgetSettings(client_daily_max_usd=0.05, per_call_max_usd=0.04))
    run_id, client_id = uuid4(), uuid4()
    scope = BudgetScope.at(NOW, run_id, client_id)
    with pytest.raises(LLMBudgetExceeded, match="per-call"):
        await ledger.reserve(scope, 0.041)
    await ledger.open_run(run_id, 0.03)
    reservation = await ledger.reserve(scope, 0.02)
    with pytest.raises(LLMBudgetExceeded, match="run"):
        await ledger.reserve(scope, 0.02)
    await ledger.settle(reservation, 0.001)
    await ledger.reserve(scope, 0.02)
    other_run = BudgetScope.at(NOW, uuid4(), client_id)
    with pytest.raises(LLMBudgetExceeded, match="client daily"):
        await ledger.reserve(other_run, 0.04)


async def test_redis_ledger_is_atomic_under_concurrency(redis: Redis, redis_prefix: str) -> None:
    ledger = RedisBudgetLedger(redis, redis_prefix, LLMBudgetSettings(client_daily_max_usd=0.10))
    client_id = uuid4()
    scope = BudgetScope.at(NOW, None, client_id)

    async def attempt() -> bool:
        try:
            await ledger.reserve(scope, 0.01)
            return True
        except LLMBudgetExceeded:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(25)))
    assert sum(results) == 10


async def test_redis_ledger_settles_and_tracks_run_caps(redis: Redis, redis_prefix: str) -> None:
    ledger = RedisBudgetLedger(redis, redis_prefix, LLMBudgetSettings())
    run_id = uuid4()
    await ledger.open_run(run_id, 0.05)
    scope = BudgetScope.at(NOW, run_id, uuid4())
    reservation = await ledger.reserve(scope, 0.04)
    await ledger.settle(reservation, 0.01)
    await ledger.reserve(scope, 0.04)
    with pytest.raises(LLMBudgetExceeded, match="run"):
        await ledger.reserve(scope, 0.01)


async def test_redis_ledger_falls_back_to_half_caps_when_redis_is_down() -> None:
    dead = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.1)
    ledger = RedisBudgetLedger(dead, "x", LLMBudgetSettings(client_daily_max_usd=0.10))
    scope = BudgetScope.at(NOW, None, uuid4())
    await ledger.reserve(scope, 0.05)
    with pytest.raises(LLMBudgetExceeded):
        await ledger.reserve(scope, 0.01)
    await dead.aclose()
