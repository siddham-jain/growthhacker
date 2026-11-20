import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, ValidationError

from app.adapters.llm.fallback_chain import ChainCall, ChainOutcome, FallbackChain, OutputInvalid, sha256
from app.adapters.llm.rendering import PromptRenderer, RenderedPrompt
from app.adapters.llm.routing import TaskRouter
from app.adapters.llm.schema_compiler import compile_schema
from app.core.config import LLMSettings, Tier
from app.core.logging import get_logger
from app.ports.clock import Clock
from app.ports.llm import (
    CallBudget,
    CallContext,
    LLMBudgetExceeded,
    LLMError,
    LLMErrorKind,
    LLMResult,
    LLMUnavailable,
    PromptInput,
    PromptRef,
    TaskId,
    TokenUsage,
)
from app.services.llm.budgets import BudgetScope, LLMBudgetLedger
from app.services.llm.costs import CostCalculator, estimate_tokens
from app.services.llm.prompts import PromptRegistry, PromptSpec, Truncator
from app.services.llm.result_cache import ResultCache, cache_key
from app.services.llm.tasks import resolve_task
from app.services.llm.usage import AttemptRecord, UsageRecorder

logger = get_logger(__name__)
TEXT_SCHEMA_NAME = "text"


def validation_problems(error: ValidationError) -> list[str]:
    # paths and messages only, so repair prompts never echo untrusted input back
    return [
        f"{'.'.join(str(part) for part in item['loc']) or 'root'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    ]


class DefaultLLMClient:
    """LLMClient port: cache -> route -> budget -> fallback chain -> accounting -> cache."""

    def __init__(
        self,
        *,
        settings: LLMSettings,
        prompts: PromptRegistry,
        chain: FallbackChain,
        budget: LLMBudgetLedger,
        costs: CostCalculator,
        cache: ResultCache,
        usage: UsageRecorder,
        clock: Clock,
        closers: tuple[Any, ...] = (),
    ) -> None:
        self._settings = settings
        self._prompts = prompts
        self._chain = chain
        self._budget = budget
        self._costs = costs
        self._cache = cache
        self._usage = usage
        self._clock = clock
        self._router = TaskRouter(settings)
        self._renderer = PromptRenderer(settings.writer.canary_salt)
        self._closers = closers

    async def generate_structured[T: BaseModel](
        self,
        *,
        task: TaskId,
        schema: type[T],
        system: PromptRef,
        input: PromptInput,
        context: CallContext,
        tier: Tier | None = None,
        budget: CallBudget | None = None,
    ) -> LLMResult[T]:
        spec = self._spec(system, task, context)
        if spec.output_schema is not None and spec.output_schema is not schema:
            raise ValueError(f"prompt '{spec.prompt_id}' is registered for {spec.output_schema.__name__}")

        def validate(data: Any) -> T:
            try:
                value = schema.model_validate(data)
            except ValidationError as error:
                raise OutputInvalid(validation_problems(error)) from None
            problems = spec.semantic_check(value, input) if spec.semantic_check else []
            if problems:
                raise OutputInvalid(problems)
            return value

        def from_cache(output: Any) -> T:
            return schema.model_validate(output)

        def to_cache(value: T) -> Any:
            return value.model_dump(mode="json")

        return await self._generate(
            task, spec, input, context, tier, budget, schema.__name__, validate, from_cache, to_cache, schema
        )

    async def generate_text(
        self,
        *,
        task: TaskId,
        system: PromptRef,
        input: PromptInput,
        context: CallContext,
        tier: Tier | None = None,
        budget: CallBudget | None = None,
    ) -> LLMResult[str]:
        spec = self._spec(system, task, context)

        def validate(text: str) -> str:
            return text.strip()

        return await self._generate(
            task, spec, input, context, tier, budget, TEXT_SCHEMA_NAME, validate, str, str, None
        )

    def _spec(self, system: PromptRef, task: TaskId, context: CallContext) -> PromptSpec:
        spec = self._prompts.resolve(system, self._settings)
        if spec.task is not task:
            raise ValueError(f"prompt '{spec.prompt_id}' belongs to task {spec.task.value}, not {task.value}")
        outside = context.payload_classes - spec.payload_classes
        if outside:
            names = ", ".join(sorted(item.value for item in outside))
            raise ValueError(f"prompt '{spec.prompt_id}' does not declare payload classes: {names}")
        return spec

    async def _generate(
        self,
        task: TaskId,
        spec: PromptSpec,
        prompt_input: PromptInput,
        context: CallContext,
        tier: Tier | None,
        budget: CallBudget | None,
        schema_name: str,
        validate: Any,
        from_cache: Any,
        to_cache: Any,
        # prompts without a registered schema take the caller's (topic ids are built per call)
        output_schema: type[BaseModel] | None,
    ) -> LLMResult[Any]:
        if not self._settings.enabled:
            raise LLMUnavailable([LLMError(LLMErrorKind.disabled, "llm.enabled is false")])
        task_spec = resolve_task(task, self._settings)
        if tier is not None:
            logger.info("llm_tier_override", task=task.value, tier=tier)
        canonical_input = prompt_input.model_dump_json()
        input_hash = sha256(canonical_input)
        key = cache_key(task.value, spec.prompt_version, canonical_input, context.language, schema_name)
        if context.idempotency_key is not None:
            hit = await self._cache.get(key)
            if hit is not None:
                await self._usage.record(
                    [
                        self._cache_record(
                            task, spec, task_spec.tier, context, input_hash, hit.model, schema_name
                        )
                    ]
                )
                return self._cached_result(task, spec, from_cache(hit.output), hit.model)
        routes = self._router.resolve(task_spec, tier)
        if not routes:
            raise LLMUnavailable([LLMError(LLMErrorKind.disabled, "no llm provider is configured")])
        rendered = self._renderer.render(spec.full_system_text, spec.prompt_version, prompt_input)
        if budget and budget.max_output_tokens:
            cap = budget.max_output_tokens
            routes = [replace(route, max_output_tokens=min(route.max_output_tokens, cap)) for route in routes]
        input_tokens = sum(
            estimate_tokens(block.text) for block in (*rendered.system_blocks, *rendered.user_blocks)
        )
        if budget and budget.max_input_tokens and input_tokens > budget.max_input_tokens:
            raise LLMBudgetExceeded(f"input estimate {input_tokens} tokens exceeds the call budget")
        estimate = self._costs.estimate(routes[0].model, input_tokens, routes[0].max_output_tokens)
        if budget and budget.max_cost_usd is not None and estimate > budget.max_cost_usd:
            raise LLMBudgetExceeded(f"call estimate ${estimate:.4f} exceeds the call budget")
        scope = BudgetScope.at(self._clock.now(), context.run_id, context.client_id)
        reservation = await self._budget.reserve(scope, estimate)
        log: list[AttemptRecord] = []
        call = ChainCall(
            task=task,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            schema_name=schema_name,
            routes=routes,
            rendered=rendered,
            static_system_text=spec.system_text,
            schema=compile_schema(spec.output_schema or output_schema) if output_schema else None,
            validate=validate,
            context=context,
            max_attempts=min(
                task_spec.max_attempts, budget.max_attempts if budget else task_spec.max_attempts
            ),
            expected_reply_tokens=task_spec.expected_reply_tokens,
            input_tokens_estimate=input_tokens,
            input_hash=input_hash,
            metadata_user_id=_opaque_user(context, self._settings.writer.canary_salt),
            shrink=_shrinker(spec.truncator, spec, prompt_input, self._renderer) if spec.truncator else None,
        )
        outcome: ChainOutcome | None = None
        try:
            outcome = await self._chain.run(call, log)
        finally:
            # also runs on cancellation, so reservations never leak
            spent = outcome.cost_usd if outcome is not None else sum(record.cost_usd or 0.0 for record in log)
            await self._budget.settle(reservation, spent)
            await self._usage.record(log)
        if context.idempotency_key is not None:
            await self._cache.put(
                key,
                task=task.value,
                prompt_version=spec.prompt_version,
                output=to_cache(outcome.value),
                model=outcome.response.model,
                ttl_days=task_spec.cache_ttl_days,
            )
        return self._result(task, spec, outcome)

    def _result(self, task: TaskId, spec: PromptSpec, outcome: ChainOutcome) -> LLMResult[Any]:
        return LLMResult[Any](
            value=outcome.value,
            raw_text=outcome.raw_text,
            task=task,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            provider=outcome.route.provider,
            model=outcome.response.model,
            route_index=outcome.route.index,
            attempts=outcome.attempts,
            repaired=outcome.repaired,
            usage=outcome.usage,
            cost_usd=outcome.cost_usd,
            latency_ms=outcome.response.latency_ms,
            stop_reason=outcome.response.stop_reason,
            request_ids=outcome.request_ids,
            cache_hit=False,
        )

    def _cache_record(
        self,
        task: TaskId,
        spec: PromptSpec,
        tier: Tier,
        context: CallContext,
        input_hash: str,
        model: str,
        schema_name: str,
    ) -> AttemptRecord:
        return AttemptRecord(
            occurred_at=self._clock.now(),
            provider="result_cache",
            operation=task.value,
            stage=context.stage,
            tier=tier,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            schema_name=schema_name,
            model_requested=model,
            model_resolved=model,
            route_index=0,
            attempt_no=0,
            attempt_kind="cache",
            outcome="ok",
            error_kind=None,
            status_code=None,
            stop_reason="cache",
            refusal_category=None,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            thinking_tokens=None,
            cost_usd=0.0,
            latency_ms=0,
            provider_request_id=None,
            input_hash=input_hash,
            output_hash=None,
            run_id=context.run_id,
            client_id=context.client_id,
            cache_hit=True,
        )

    def _cached_result(self, task: TaskId, spec: PromptSpec, value: Any, model: str) -> LLMResult[Any]:
        return LLMResult[Any](
            value=value,
            raw_text="",
            task=task,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            provider="result_cache",
            model=model,
            route_index=0,
            attempts=0,
            repaired=False,
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=0,
            stop_reason="cache",
            cache_hit=True,
        )

    async def aclose(self) -> None:
        for closer in self._closers:
            await closer.aclose()


def _opaque_user(context: CallContext, salt: str) -> str | None:
    if context.client_id is None:
        return None
    return hashlib.sha256(f"{salt}:{context.client_id}".encode()).hexdigest()[:32]


def _shrinker(
    truncator: Truncator, spec: PromptSpec, prompt_input: PromptInput, renderer: PromptRenderer
) -> Callable[[], RenderedPrompt | None]:
    def shrink() -> RenderedPrompt | None:
        smaller = truncator(prompt_input)
        if smaller is None:
            return None
        return renderer.render(spec.full_system_text, spec.prompt_version, smaller)

    return shrink
