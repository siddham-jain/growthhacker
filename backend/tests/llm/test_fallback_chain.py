import asyncio
import json
from typing import Any

import pytest

from app.adapters.llm.circuit_breaker import CircuitBreaker, MemoryCircuitStore
from app.adapters.llm.testing import FakeProvider, fake_response
from app.ports.llm import (
    CallBudget,
    CallContext,
    LLMBudgetExceeded,
    LLMError,
    LLMErrorKind,
    LLMUnavailable,
    PayloadClass,
    PromptInput,
    PromptRef,
    TaskId,
    TokenUsage,
    TrustedBlock,
)
from app.services.llm.prompts import PromptRegistry, PromptSpec
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.specs import CARD_WRITE_PROMPT
from tests.llm.factories import (
    CLIENT_ID,
    GOOD_COPY,
    RUN_ID,
    FakeMonotonic,
    Sleeps,
    make_client,
    make_llm_settings,
)

GOOD = json.dumps(GOOD_COPY)
INPUT = PromptInput(trusted=[TrustedBlock(label="evidence_pack", text='{"facts":[]}')])


def context(*, deadline: float | None = None, private: bool = False, key: str | None = None) -> CallContext:
    classes = {PayloadClass.public_youtube}
    if private:
        classes.add(PayloadClass.client_private_analytics)
    return CallContext(
        run_id=RUN_ID,
        client_id=CLIENT_ID,
        stage="write_cards",
        deadline=deadline,
        payload_classes=frozenset(classes),
        idempotency_key=key,
    )


async def generate(client: Any, **overrides: Any):
    arguments = {
        "task": TaskId.card_write,
        "schema": CardCopyLLM,
        "system": CARD_WRITE_PROMPT,
        "input": INPUT,
        "context": context(),
    }
    arguments.update(overrides)
    return await client.generate_structured(**arguments)


def error(kind: LLMErrorKind, **extra: Any) -> LLMError:
    return LLMError(kind, kind.value, **extra)


async def test_medium_tier_routes_sonnet_first_with_low_effort() -> None:
    anthropic = FakeProvider("anthropic", [fake_response(GOOD, model="claude-sonnet-5-5")])
    client, usage, _ = make_client(
        {"anthropic": anthropic, "openai_compat": FakeProvider("openai_compat", [])}
    )
    result = await generate(client)
    sent = anthropic.requests[0]
    assert sent.model == "claude-sonnet-5-5"
    assert sent.effort == "low"
    assert sent.max_output_tokens >= 2 * 600
    assert result.value == CardCopyLLM.model_validate(GOOD_COPY)
    assert result.provider == "anthropic" and result.attempts == 1 and not result.repaired
    assert [record.attempt_kind for record in usage.attempts] == ["initial"]
    assert usage.attempts[0].output_hash is not None


async def test_rate_limit_with_short_retry_after_retries_same_route() -> None:
    sleeps = Sleeps()
    anthropic = FakeProvider(
        "anthropic", [error(LLMErrorKind.rate_limited, retry_after_s=1.5), fake_response(GOOD)]
    )
    client, usage, _ = make_client({"anthropic": anthropic}, sleeps=sleeps)
    result = await generate(client)
    assert result.attempts == 2
    assert sleeps.delays == [1.5]
    assert [record.attempt_kind for record in usage.attempts] == ["initial", "retry"]


async def test_rate_limit_without_retry_after_falls_back() -> None:
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.rate_limited)])
    gateway = FakeProvider("openai_compat", [fake_response(GOOD, model="gw-medium")])
    client, usage, _ = make_client({"anthropic": anthropic, "openai_compat": gateway})
    result = await generate(client)
    assert result.provider == "openai_compat" and result.route_index == 1
    assert usage.attempts[-1].attempt_kind == "fallback"


async def test_overloaded_twice_then_fallback() -> None:
    settings = make_llm_settings(anthropic={"api_key": "k", "medium": {"max_retries": 1}})
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.overloaded), error(LLMErrorKind.overloaded)])
    gateway = FakeProvider("openai_compat", [fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic, "openai_compat": gateway}, settings)
    result = await generate(client)
    assert len(anthropic.requests) == 2
    assert result.provider == "openai_compat"


async def test_refusal_moves_to_a_different_model() -> None:
    anthropic = FakeProvider(
        "anthropic",
        [fake_response("", stop_reason="refusal", refusal_category="bio"), fake_response(GOOD)],
    )
    gateway = FakeProvider("openai_compat", [error(LLMErrorKind.server_error)])
    settings = make_llm_settings(
        openai_compat={
            "api_key": "k",
            "base_url": "http://gateway.test/v1",
            "medium": {"model": "gw-medium", "max_retries": 0},
            "low": {"model": "gw-low"},
        }
    )
    client, usage, _ = make_client({"anthropic": anthropic, "openai_compat": gateway}, settings)
    result = await generate(client)
    assert [request.model for request in anthropic.requests] == ["claude-sonnet-5-5", "claude-haiku-4-5"]
    assert result.model == "claude-haiku-4-5-20251001"
    assert usage.attempts[0].error_kind == "refusal"
    assert usage.attempts[0].refusal_category == "bio"


async def test_truncation_doubles_max_tokens_and_lowers_effort_once() -> None:
    settings = make_llm_settings(anthropic={"api_key": "k", "medium": {"effort": "medium"}})
    anthropic = FakeProvider("anthropic", [fake_response("{", stop_reason="max_tokens"), fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic}, settings)
    await generate(client)
    first, second = anthropic.requests
    assert second.max_output_tokens == 2 * first.max_output_tokens
    assert (first.effort, second.effort) == ("medium", "low")


async def test_invalid_output_gets_one_repair_turn() -> None:
    anthropic = FakeProvider("anthropic", [fake_response('{"summary": 3}'), fake_response(GOOD)])
    client, usage, _ = make_client({"anthropic": anthropic})
    result = await generate(client)
    assert result.repaired
    repair = anthropic.requests[1].followups
    assert [turn.role for turn in repair] == ["assistant", "user"]
    assert "failed validation" in repair[1].text
    assert usage.attempts[1].attempt_kind == "repair"


async def test_repair_failure_falls_through_the_chain() -> None:
    anthropic = FakeProvider("anthropic", [fake_response("not json"), fake_response("still not json")])
    gateway = FakeProvider("openai_compat", [fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic, "openai_compat": gateway})
    result = await generate(client)
    assert result.provider == "openai_compat"


async def test_canary_leak_is_invalid_output() -> None:
    def leak(request: Any) -> Any:
        marker = request.system_blocks[0].text.rsplit(": ", 1)[-1]
        return fake_response(json.dumps({**GOOD_COPY, "summary": f"marker {marker}"}))

    anthropic = FakeProvider("anthropic", [leak, fake_response(GOOD)])
    client, usage, _ = make_client({"anthropic": anthropic})
    result = await generate(client)
    assert result.repaired
    assert usage.attempts[0].error_kind == "invalid_output"


async def test_everything_failing_raises_unavailable_with_every_attempt() -> None:
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.auth), error(LLMErrorKind.auth)])
    gateway = FakeProvider(
        "openai_compat", [error(LLMErrorKind.bad_request), error(LLMErrorKind.bad_request)]
    )
    client, usage, _ = make_client({"anthropic": anthropic, "openai_compat": gateway})
    with pytest.raises(LLMUnavailable) as raised:
        await generate(client)
    kinds = [attempt.kind for attempt in raised.value.attempts]
    assert LLMErrorKind.auth in kinds and LLMErrorKind.bad_request in kinds
    assert all(record.outcome == "error" for record in usage.attempts)


async def test_auth_failure_hard_opens_the_provider() -> None:
    store = MemoryCircuitStore()
    settings = make_llm_settings()
    breaker = CircuitBreaker(settings.resilience, store, config_hash="test")
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.auth)])
    gateway = FakeProvider("openai_compat", [fake_response(GOOD), fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic, "openai_compat": gateway}, settings, breaker=breaker)
    await generate(client)
    await generate(client, input=PromptInput(trusted=[TrustedBlock(label="evidence_pack", text="other")]))
    assert len(anthropic.requests) == 1
    assert not await breaker.allow("anthropic:claude-sonnet-5-5", "anthropic")


async def test_private_analytics_never_reach_the_fallback_provider() -> None:
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.overloaded)] * 3)
    gateway = FakeProvider("openai_compat", [fake_response(GOOD)])
    settings = make_llm_settings(
        anthropic={"api_key": "k", "medium": {"max_retries": 0}, "low": {"max_retries": 0}}
    )
    client, _, _ = make_client({"anthropic": anthropic, "openai_compat": gateway}, settings)
    with pytest.raises(LLMUnavailable) as raised:
        await generate(client, context=context(private=True))
    assert gateway.requests == []
    assert LLMErrorKind.data_policy_blocked in {attempt.kind for attempt in raised.value.attempts}


async def test_deadline_stops_the_chain_before_doomed_attempts() -> None:
    clock = FakeMonotonic()
    anthropic = FakeProvider("anthropic", [fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic}, monotonic=clock)
    with pytest.raises(LLMUnavailable) as raised:
        await generate(client, context=context(deadline=clock.now + 3))
    assert anthropic.requests == []
    assert raised.value.attempts[-1].kind is LLMErrorKind.deadline_exceeded


async def test_request_timeout_is_capped_by_the_deadline() -> None:
    clock = FakeMonotonic()
    anthropic = FakeProvider("anthropic", [fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic}, monotonic=clock)
    await generate(client, context=context(deadline=clock.now + 12))
    assert anthropic.requests[0].timeout_s == 12


async def test_attempt_cap_bounds_retries() -> None:
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.server_error)] * 10)
    gateway = FakeProvider("openai_compat", [error(LLMErrorKind.server_error)] * 10)
    client, usage, _ = make_client({"anthropic": anthropic, "openai_compat": gateway})
    with pytest.raises(LLMUnavailable):
        await generate(client, budget=CallBudget(max_attempts=2))
    assert len(usage.attempts) == 2


async def test_result_cache_makes_repeat_calls_free_and_identical() -> None:
    anthropic = FakeProvider("anthropic", [fake_response(GOOD)])
    client, usage, _ = make_client({"anthropic": anthropic})
    first = await generate(client, context=context(key="k1"))
    second = await generate(client, context=context(key="k1"))
    assert second.cache_hit and second.cost_usd == 0
    assert second.value == first.value
    assert len(anthropic.requests) == 1
    assert [(record.attempt_kind, record.cache_hit) for record in usage.attempts] == [
        ("initial", False),
        ("cache", True),
    ]


async def test_no_idempotency_key_bypasses_the_cache() -> None:
    anthropic = FakeProvider("anthropic", [fake_response(GOOD), fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic})
    await generate(client)
    await generate(client)
    assert len(anthropic.requests) == 2


async def test_disabled_mode_raises_without_calling_providers() -> None:
    anthropic = FakeProvider("anthropic", [])
    client, _, _ = make_client({"anthropic": anthropic}, make_llm_settings(enabled=False))
    with pytest.raises(LLMUnavailable) as raised:
        await generate(client)
    assert raised.value.kind is LLMErrorKind.disabled


async def test_budget_exhaustion_raises_before_any_call() -> None:
    settings = make_llm_settings(budgets={"client_daily_max_usd": 0.001})
    anthropic = FakeProvider("anthropic", [])
    client, _, _ = make_client({"anthropic": anthropic}, settings)
    with pytest.raises(LLMBudgetExceeded):
        await generate(client)
    assert anthropic.requests == []


async def test_budget_is_settled_with_actual_cost() -> None:
    usage_tokens = TokenUsage(input_tokens=1000, output_tokens=100)
    anthropic = FakeProvider(
        "anthropic", [fake_response(GOOD, model="claude-sonnet-5-5", usage=usage_tokens)]
    )
    client, _, budget = make_client({"anthropic": anthropic})
    result = await generate(client)
    assert result.cost_usd == pytest.approx((1000 * 2 + 100 * 10) / 1_000_000)
    assert budget.used_usd(f"client:{CLIENT_ID}:20261120") == pytest.approx(result.cost_usd)


async def test_undeclared_payload_class_is_a_programming_error() -> None:
    client, _, _ = make_client({"anthropic": FakeProvider("anthropic", [])})
    bad = context()
    bad = bad.model_copy(update={"payload_classes": frozenset({PayloadClass.comments_redacted})})
    with pytest.raises(ValueError, match="payload classes"):
        await generate(client, context=bad)


async def test_cancellation_propagates_and_releases_the_reservation() -> None:
    started = asyncio.Event()

    async def hang(request: Any) -> Any:
        started.set()
        await asyncio.sleep(60)

    class HangingProvider(FakeProvider):
        async def complete(self, request: Any) -> Any:
            return await hang(request)

    client, _, budget = make_client({"anthropic": HangingProvider("anthropic", [])})
    task = asyncio.ensure_future(generate(client))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.used_usd(f"client:{CLIENT_ID}:20261120") == 0


async def test_prompt_too_long_shrinks_once_with_the_task_truncator() -> None:
    registry = PromptRegistry()
    registry.register(
        PromptSpec(
            prompt_id="shrinkable",
            task=TaskId.card_write,
            version=1,
            system_text="rules",
            payload_classes=frozenset({PayloadClass.public_youtube}),
            output_schema=CardCopyLLM,
            truncator=lambda prompt: prompt.model_copy(update={"trusted": prompt.trusted[:1]}),
        )
    )
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.context_too_long), fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic}, prompts=registry)
    long_input = PromptInput(
        trusted=[TrustedBlock(label="page", text="keep"), TrustedBlock(label="page", text="drop me")]
    )
    await generate(client, system=PromptRef(prompt_id="shrinkable"), input=long_input)
    assert "drop me" in anthropic.requests[0].user_blocks[-1].text
    assert "drop me" not in anthropic.requests[1].user_blocks[-1].text


async def test_spend_cap_opens_the_provider_circuit() -> None:
    settings = make_llm_settings()
    breaker = CircuitBreaker(settings.resilience, MemoryCircuitStore(), config_hash="test")
    anthropic = FakeProvider("anthropic", [error(LLMErrorKind.spend_cap)])
    gateway = FakeProvider("openai_compat", [fake_response(GOOD)])
    client, _, _ = make_client({"anthropic": anthropic, "openai_compat": gateway}, settings, breaker=breaker)
    await generate(client)
    assert not await breaker.allow("anthropic:claude-haiku-4-5", "anthropic")
    await breaker.reset("anthropic:*")
    assert await breaker.allow("anthropic:claude-haiku-4-5", "anthropic")
