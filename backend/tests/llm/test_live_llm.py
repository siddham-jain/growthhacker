from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from app.adapters.llm.circuit_breaker import CircuitBreaker, MemoryCircuitStore
from app.adapters.llm.client import DefaultLLMClient
from app.adapters.llm.factory import build_providers
from app.adapters.llm.fallback_chain import FallbackChain
from app.adapters.llm.health import check_llm_health
from app.adapters.llm.limiter import AdaptiveLimiter, RunLimiter
from app.adapters.llm.retry_policy import RetryPolicy
from app.core.clock import SystemClock
from app.core.config import LLMSettings, Settings
from app.ports.llm import CallContext, PayloadClass, PromptInput, PromptRef, TaskId, TrustedBlock
from app.services.llm.budgets import MemoryBudgetLedger
from app.services.llm.costs import CostCalculator
from app.services.llm.prompts import PromptRegistry, PromptSpec, prompt_registry
from app.services.llm.result_cache import MemoryResultCache
from app.services.llm.usage import MemoryUsageRecorder
from app.services.llm.writer.evidence_pack import EvidencePackBuilder
from app.services.llm.writer.schemas import CardCopyLLM, ReasonLine
from app.services.llm.writer.service import build_card_writing_service
from app.services.llm.writer.validators import ValidatorSuite, blocking
from tests.llm.factories import CLIENT_ID, RUN_ID, make_client_context, make_draft

pytestmark = pytest.mark.live


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_kind: Literal["clinic", "dealer", "other"]
    keyword: str


PROBE = PromptSpec(
    prompt_id="live_probe",
    task=TaskId.research_query_planning,
    version=1,
    system_text="Classify the business described in the input and return one search keyword for it.",
    payload_classes=frozenset({PayloadClass.client_profile}),
    output_schema=Probe,
)


def live_settings() -> LLMSettings:
    settings = Settings().llm
    if settings.anthropic.api_key is None:
        pytest.skip("ANTHROPIC_API_KEY is not configured")
    # anthropic only, so every tier is exercised on the primary provider
    return settings.model_copy(update={"provider_order": ["anthropic"]})


def live_client(
    settings: LLMSettings, prompts: PromptRegistry
) -> tuple[DefaultLLMClient, MemoryUsageRecorder]:
    clock = SystemClock()
    providers = {
        name: provider for name, provider in build_providers(settings).items() if name == "anthropic"
    }
    costs = CostCalculator(settings.prices)
    usage = MemoryUsageRecorder()
    chain = FallbackChain(
        settings=settings,
        providers=providers,
        limiters={"anthropic": AdaptiveLimiter(4)},
        run_limiter=RunLimiter(4),
        breaker=CircuitBreaker(settings.resilience, MemoryCircuitStore(), config_hash="live"),
        policy=RetryPolicy(settings.resilience),
        costs=costs,
        clock=clock,
    )
    client = DefaultLLMClient(
        settings=settings,
        prompts=prompts,
        chain=chain,
        budget=MemoryBudgetLedger(settings.budgets),
        costs=costs,
        cache=MemoryResultCache(clock),
        usage=usage,
        clock=clock,
        closers=tuple(providers.values()),
    )
    return client, usage


@pytest.mark.parametrize(
    ("tier", "model"),
    [("low", "claude-haiku-4-5"), ("medium", "claude-sonnet-5-5"), ("high", "claude-sonnet-5-5")],
)
async def test_structured_call_on_each_tier(tier: Literal["low", "medium", "high"], model: str) -> None:
    registry = PromptRegistry()
    registry.register(PROBE)
    client, usage = live_client(live_settings(), registry)
    try:
        result = await client.generate_structured(
            task=TaskId.research_query_planning,
            schema=Probe,
            system=PromptRef(prompt_id="live_probe"),
            input=PromptInput(trusted=[TrustedBlock(label="business", text="A dental clinic in Pune.")]),
            context=CallContext(
                run_id=None,
                client_id=None,
                stage="live_smoke",
                payload_classes=frozenset({PayloadClass.client_profile}),
            ),
            tier=tier,
        )
    finally:
        await client.aclose()
    assert result.value.channel_kind == "clinic"
    assert result.model.startswith(model)
    assert result.provider == "anthropic" and result.route_index == 0
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    assert result.request_ids and result.cost_usd > 0
    assert usage.attempts[-1].outcome == "ok"


async def test_health_check_matches_the_capability_table() -> None:
    report = await check_llm_health(live_settings())
    assert {(item.model, item.status) for item in report} == {
        ("claude-haiku-4-5", "ok"),
        ("claude-sonnet-5-5", "ok"),
    }, report


async def test_writer_on_a_fixture_draft() -> None:
    settings = live_settings()
    client, usage = live_client(settings, prompt_registry)
    service = build_card_writing_service(client, settings.writer)
    draft = make_draft()
    context = make_client_context()
    try:
        written = await service.write([draft], context, run_id=RUN_ID, client_id=CLIENT_ID, deadline_s=90)
    finally:
        await client.aclose()
    card = written.cards[0].card_copy
    assert card.copy_source in {"llm", "llm_revised"}, (card.degrade_reason, card.violations_last)
    assert len(usage.attempts) <= 8
    built = EvidencePackBuilder().build(draft, context)
    replay = CardCopyLLM(
        summary=card.summary,
        working_title=card.working_title,
        why_it_works=[
            ReasonLine(
                fact_refs=[alias for alias, rid in built.grounding.reason_ids.items() if rid in ids],
                text=text,
            )
            for text, ids in zip(card.why_it_works, card.reason_fact_ids, strict=True)
        ],
        fit_to_client=ReasonLine(
            fact_refs=[
                alias for alias, rid in built.grounding.reason_ids.items() if rid in card.fit_fact_ids
            ],
            text=card.fit_to_client,
        ),
    )
    assert blocking(ValidatorSuite(settings.writer).run(replay, built)) == []
    print(card.model_dump_json(indent=2))
