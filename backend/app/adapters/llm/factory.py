import hashlib
from typing import Any

from app.adapters.llm.circuit_breaker import CircuitBreaker, RedisCircuitStore
from app.adapters.llm.client import DefaultLLMClient
from app.adapters.llm.fallback_chain import FallbackChain
from app.adapters.llm.limiter import AdaptiveLimiter, RunLimiter
from app.adapters.llm.providers.anthropic_provider import AnthropicProvider
from app.adapters.llm.providers.base import ChatProvider
from app.adapters.llm.providers.openai_compat import OpenAICompatibleProvider
from app.adapters.llm.retry_policy import RetryPolicy
from app.core.config import LLMSettings
from app.core.registry import adapter_registry
from app.services.llm.budgets import RedisBudgetLedger
from app.services.llm.costs import CostCalculator
from app.services.llm.prompts import prompt_registry
from app.services.llm.result_cache import SafeResultCache, SqlResultCache
from app.services.llm.usage import SafeUsageRecorder, SqlUsageRecorder


def settings_hash(settings: LLMSettings) -> str:
    # secrets render masked, so a rotated key does not show here; hard-open circuits also clear on reset
    return hashlib.sha256(settings.model_dump_json().encode("utf-8")).hexdigest()[:16]


def build_providers(settings: LLMSettings) -> dict[str, ChatProvider]:
    providers: dict[str, ChatProvider] = {}
    if settings.anthropic.configured:
        providers["anthropic"] = AnthropicProvider(settings.anthropic)
    if settings.openai_compat.configured:
        providers["openai_compat"] = OpenAICompatibleProvider(settings.openai_compat)
    return providers


@adapter_registry.register("llm", "fallback_chain")
def build_llm_client(container: Any) -> DefaultLLMClient:
    settings: LLMSettings = container.settings.llm
    resilience = settings.resilience
    prefix = container.settings.redis.key_prefix
    providers = build_providers(settings)
    limiters = {
        name: AdaptiveLimiter(
            settings.provider(name).max_concurrency,
            cooldown_s=resilience.aimd_cooldown_s,
            recovery_successes=resilience.aimd_recovery_successes,
        )
        for name in providers
    }
    costs = CostCalculator(settings.prices)
    chain = FallbackChain(
        settings=settings,
        providers=providers,
        limiters=limiters,
        run_limiter=RunLimiter(resilience.per_run_concurrency),
        breaker=CircuitBreaker(
            resilience, RedisCircuitStore(container.redis, prefix), config_hash=settings_hash(settings)
        ),
        policy=RetryPolicy(resilience),
        costs=costs,
        clock=container.clock,
    )
    return DefaultLLMClient(
        settings=settings,
        prompts=prompt_registry,
        chain=chain,
        budget=RedisBudgetLedger(container.redis, prefix, settings.budgets),
        costs=costs,
        cache=SafeResultCache(SqlResultCache(container.session_factory, container.clock)),
        usage=SafeUsageRecorder(SqlUsageRecorder(container.session_factory)),
        clock=container.clock,
        closers=tuple(providers.values()),
    )
