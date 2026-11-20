import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.adapters.llm.capabilities import lower_effort, sized_max_tokens
from app.adapters.llm.circuit_breaker import CircuitBreaker, provider_key
from app.adapters.llm.dialects import schema_instruction
from app.adapters.llm.limiter import AdaptiveLimiter, RunLimiter
from app.adapters.llm.providers.base import ChatProvider
from app.adapters.llm.rendering import RenderedPrompt
from app.adapters.llm.retry_policy import Action, CircuitEffect, RetryPolicy
from app.adapters.llm.types import CompiledSchema, ProviderRequest, ProviderResponse, Route, TextBlock, Turn
from app.core.config import LLMSettings
from app.ports.clock import Clock
from app.ports.llm import CallContext, LLMError, LLMErrorKind, LLMUnavailable, TaskId, TokenUsage
from app.services.llm.costs import CostCalculator
from app.services.llm.usage import AttemptKind, AttemptRecord

LEAK_WINDOW = 60
MAX_REPAIR_PROBLEMS = 10


class OutputInvalid(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems[:MAX_REPAIR_PROBLEMS]


@dataclass(slots=True)
class ChainCall:
    task: TaskId
    prompt_id: str
    prompt_version: str
    schema_name: str | None
    routes: list[Route]
    rendered: RenderedPrompt
    static_system_text: str
    schema: CompiledSchema | None
    # turns parsed json (structured) or raw text into the validated value, raising OutputInvalid
    validate: Callable[[Any], Any]
    context: CallContext
    max_attempts: int
    expected_reply_tokens: int
    input_tokens_estimate: int
    input_hash: str
    metadata_user_id: str | None = None
    shrink: Callable[[], RenderedPrompt | None] | None = None


@dataclass(slots=True)
class ChainOutcome:
    value: Any
    raw_text: str
    response: ProviderResponse
    route: Route
    attempts: int
    repaired: bool
    usage: TokenUsage
    cost_usd: float
    request_ids: list[str] = field(default_factory=list)


def add_usage(total: TokenUsage, extra: TokenUsage) -> TokenUsage:
    thinking = None
    if total.thinking_tokens is not None or extra.thinking_tokens is not None:
        thinking = (total.thinking_tokens or 0) + (extra.thinking_tokens or 0)
    return TokenUsage(
        input_tokens=total.input_tokens + extra.input_tokens,
        cache_write_tokens=total.cache_write_tokens + extra.cache_write_tokens,
        cache_read_tokens=total.cache_read_tokens + extra.cache_read_tokens,
        output_tokens=total.output_tokens + extra.output_tokens,
        thinking_tokens=thinking,
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repair_message(problems: list[str], structured: bool) -> str:
    listed = "\n".join(f"- {problem}" for problem in problems)
    if structured:
        return f"Your previous output failed validation:\n{listed}\nReturn the corrected JSON object only."
    return f"Your previous reply was not usable:\n{listed}\nReply again, following the rules."


class _CallState:
    """mutable bookkeeping for one generate call across every route and attempt."""

    def __init__(self, call: ChainCall, log: list[AttemptRecord]) -> None:
        self.call = call
        self.log = log
        self.errors: list[LLMError] = []
        self.refused_models: set[str] = set()
        self.attempts = 0
        self.usage = TokenUsage()
        self.cost = 0.0
        self.request_ids: list[str] = []
        self.rendered = call.rendered


class FallbackChain:
    """retry vs fallback vs fail, per the retry policy table; every attempt is logged for accounting."""

    def __init__(
        self,
        *,
        settings: LLMSettings,
        providers: Mapping[str, ChatProvider],
        limiters: Mapping[str, AdaptiveLimiter],
        run_limiter: RunLimiter,
        breaker: CircuitBreaker,
        policy: RetryPolicy,
        costs: CostCalculator,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._resilience = settings.resilience
        self._providers = providers
        self._limiters = limiters
        self._run_limiter = run_limiter
        self._breaker = breaker
        self._policy = policy
        self._costs = costs
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic

    def _remaining(self, deadline: float | None) -> float | None:
        return None if deadline is None else deadline - self._monotonic()

    def _data_allowed(self, provider: str, call: ChainCall) -> bool:
        allowed = set(self._settings.provider(provider).allowed_data_classes)
        return {payload.value for payload in call.context.payload_classes} <= allowed

    async def run(self, call: ChainCall, log: list[AttemptRecord]) -> ChainOutcome:
        state = _CallState(call, log)
        for route in call.routes:
            remaining = self._remaining(call.context.deadline)
            if remaining is not None and remaining < self._resilience.min_viable_s:
                state.errors.append(LLMError(LLMErrorKind.deadline_exceeded, "stage deadline reached"))
                break
            skip = await self._skip_reason(route, call, state)
            if skip is not None:
                state.errors.append(skip)
                continue
            outcome = await self._run_route(route, state)
            if outcome is not None:
                return outcome
        raise LLMUnavailable(state.errors)

    async def _skip_reason(self, route: Route, call: ChainCall, state: _CallState) -> LLMError | None:
        detail = {"provider": route.provider, "model": route.model}
        if route.provider not in self._providers:
            return LLMError(LLMErrorKind.disabled, "provider is not configured in this process", **detail)
        if route.model in state.refused_models:
            return LLMError(LLMErrorKind.refusal, "model already refused this input", **detail)
        if not self._data_allowed(route.provider, call):
            return LLMError(LLMErrorKind.data_policy_blocked, "provider may not receive this data", **detail)
        if not await self._breaker.allow(route.key, route.provider):
            return LLMError(LLMErrorKind.circuit_open, "circuit open", **detail)
        capabilities = self._providers[route.provider].capabilities(route.model)
        if call.input_tokens_estimate > self._resilience.context_window_share * capabilities.context_window:
            return LLMError(LLMErrorKind.context_too_long, "input exceeds the route context window", **detail)
        return None

    def _request(
        self,
        route: Route,
        state: _CallState,
        provider: ChatProvider,
        followups: list[Turn],
        max_out: int,
        effort: Any,
    ) -> ProviderRequest:
        call = state.call
        system_blocks = state.rendered.system_blocks
        if call.schema is not None and provider.schema_in_prompt():
            system_blocks = (*system_blocks, TextBlock(schema_instruction(call.schema)))
        remaining = self._remaining(call.context.deadline)
        timeout = route.timeout_s if remaining is None else max(1.0, min(route.timeout_s, remaining))
        return ProviderRequest(
            model=route.model,
            system_blocks=system_blocks,
            user_blocks=state.rendered.user_blocks,
            followups=tuple(followups),
            output_schema=call.schema,
            max_output_tokens=max_out,
            timeout_s=timeout,
            effort=effort,
            metadata_user_id=call.metadata_user_id,
        )

    async def _run_route(self, route: Route, state: _CallState) -> ChainOutcome | None:
        call = state.call
        provider = self._providers[route.provider]
        capabilities = provider.capabilities(route.model)
        max_out = sized_max_tokens(capabilities, route.max_output_tokens, call.expected_reply_tokens)
        effort = route.effort
        followups: list[Turn] = []
        retries = 0
        truncation_retried = shrunk = repaired = False
        kind: AttemptKind = "initial" if state.attempts == 0 else "fallback"
        while True:
            if state.attempts >= call.max_attempts:
                state.errors.append(LLMError(LLMErrorKind.disabled, "attempt cap reached for this call"))
                raise LLMUnavailable(state.errors)
            remaining = self._remaining(call.context.deadline)
            if remaining is not None and remaining < self._resilience.min_viable_s:
                state.errors.append(LLMError(LLMErrorKind.deadline_exceeded, "stage deadline reached"))
                raise LLMUnavailable(state.errors)
            request = self._request(route, state, provider, followups, max_out, effort)
            state.attempts += 1
            started = self._monotonic()
            try:
                async with self._run_limiter.slot(call.context.run_id), self._limiters[route.provider].slot():
                    response = await provider.complete(request)
            except LLMError as error:
                latency = int((self._monotonic() - started) * 1000)
                self._log_error(state, route, kind, error, latency)
                rule = self._policy.rule(error)
                await self._apply_circuit(rule.circuit, route)
                if error.kind is LLMErrorKind.rate_limited:
                    self._limiters[route.provider].on_rate_limited()
                if rule.action is Action.retry and retries < route.max_retries:
                    delay = self._policy.retry_delay(
                        error,
                        retries,
                        remaining_s=self._remaining(call.context.deadline),
                        timeout_s=route.timeout_s,
                    )
                    if delay is not None:
                        retries += 1
                        kind = "retry"
                        await self._sleep(delay)
                        continue
                if rule.action is Action.shrink and not shrunk and call.shrink is not None:
                    smaller = call.shrink()
                    if smaller is not None:
                        shrunk = True
                        state.rendered = smaller
                        kind = "retry"
                        continue
                if rule.action is Action.next_model:
                    state.refused_models.add(route.model)
                return None
            state.usage = add_usage(state.usage, response.usage)
            cost = self._attempt_cost(response)
            state.cost += cost
            if response.request_id:
                state.request_ids.append(response.request_id)
            if response.stop_reason in {"refusal", "content_filter"}:
                error = LLMError(
                    LLMErrorKind.refusal,
                    response.refusal_category or "refused",
                    provider=route.provider,
                    model=route.model,
                )
                self._log_response(state, route, kind, response, cost, error)
                state.refused_models.add(route.model)
                return None
            if response.stop_reason == "max_tokens":
                error = LLMError(
                    LLMErrorKind.truncated,
                    "output hit max_tokens",
                    provider=route.provider,
                    model=route.model,
                )
                self._log_response(state, route, kind, response, cost, error)
                if not truncation_retried and max_out < capabilities.max_output_tokens:
                    truncation_retried = True
                    max_out = min(2 * max_out, capabilities.max_output_tokens)
                    effort = lower_effort(effort)
                    kind = "retry"
                    continue
                return None
            try:
                value = self._parse(provider, response.text, state)
            except OutputInvalid as invalid:
                error_kind = (
                    LLMErrorKind.empty_output if not response.text.strip() else LLMErrorKind.invalid_output
                )
                error = LLMError(error_kind, invalid.problems[0], provider=route.provider, model=route.model)
                self._log_response(state, route, kind, response, cost, error)
                remaining = self._remaining(call.context.deadline)
                if not repaired and (remaining is None or remaining >= route.timeout_s / 2):
                    repaired = True
                    if response.text.strip():
                        followups = [
                            *followups,
                            Turn("assistant", response.text),
                            Turn("user", repair_message(invalid.problems, call.schema is not None)),
                        ]
                    kind = "repair"
                    continue
                return None
            self._log_response(state, route, kind, response, cost, None)
            await self._breaker.record_success(route.key)
            await self._limiters[route.provider].on_success()
            return ChainOutcome(
                value=value,
                raw_text=response.text,
                response=response,
                route=route,
                attempts=state.attempts,
                repaired=repaired,
                usage=state.usage,
                cost_usd=state.cost,
                request_ids=state.request_ids,
            )

    def _parse(self, provider: ChatProvider, text: str, state: _CallState) -> Any:
        if not text.strip():
            raise OutputInvalid(["the response was empty"])
        if state.rendered.canary in text or self._leaks_system(text, state.call.static_system_text):
            raise OutputInvalid(["the response repeated confidential instructions"])
        if state.call.schema is None:
            return state.call.validate(text)
        try:
            data = provider.extract_json(text)
        except ValueError:
            raise OutputInvalid(["the response was not a valid JSON object"]) from None
        return state.call.validate(data)

    @staticmethod
    def _leaks_system(text: str, system_text: str) -> bool:
        if len(system_text) < LEAK_WINDOW:
            return False
        step = LEAK_WINDOW // 2
        return any(
            system_text[start : start + LEAK_WINDOW] in text
            for start in range(0, len(system_text) - LEAK_WINDOW + 1, step)
        )

    def _attempt_cost(self, response: ProviderResponse) -> float:
        cost = self._costs.cost(response.model, response.usage)
        return cost if cost is not None else self._costs.ceiling_cost(response.usage)

    async def _apply_circuit(self, effect: CircuitEffect, route: Route) -> None:
        if effect is CircuitEffect.failure:
            await self._breaker.record_failure(route.key)
        elif effect is CircuitEffect.open_spend_cap:
            await self._breaker.open_for(provider_key(route.provider), self._resilience.spend_cap_open_s)
        elif effect is CircuitEffect.hard_open_provider:
            await self._breaker.hard_open(provider_key(route.provider))
        elif effect is CircuitEffect.hard_open_route:
            await self._breaker.hard_open(route.key)

    def _base_record(self, state: _CallState, route: Route, kind: AttemptKind) -> dict[str, Any]:
        call = state.call
        return {
            "occurred_at": self._clock.now(),
            "provider": route.provider,
            "operation": call.task.value,
            "stage": call.context.stage,
            "tier": route.tier,
            "prompt_id": call.prompt_id,
            "prompt_version": call.prompt_version,
            "schema_name": call.schema_name,
            "model_requested": route.model,
            "route_index": route.index,
            "attempt_no": state.attempts,
            "attempt_kind": kind,
            "input_hash": call.input_hash,
            "run_id": call.context.run_id,
            "client_id": call.context.client_id,
            "degraded_route": route.degraded,
        }

    def _log_error(
        self, state: _CallState, route: Route, kind: AttemptKind, error: LLMError, latency_ms: int
    ) -> None:
        state.errors.append(error)
        state.log.append(
            AttemptRecord(
                **self._base_record(state, route, kind),
                model_resolved=None,
                outcome="error",
                error_kind=error.kind.value,
                status_code=error.status_code,
                stop_reason=None,
                refusal_category=None,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                thinking_tokens=None,
                cost_usd=0.0,
                latency_ms=latency_ms,
                provider_request_id=error.request_id,
                output_hash=None,
            )
        )

    def _log_response(
        self,
        state: _CallState,
        route: Route,
        kind: AttemptKind,
        response: ProviderResponse,
        cost: float,
        error: LLMError | None,
    ) -> None:
        if error is not None:
            state.errors.append(error)
        usage = response.usage
        state.log.append(
            AttemptRecord(
                **self._base_record(state, route, kind),
                model_resolved=response.model,
                outcome="ok" if error is None else "error",
                error_kind=None if error is None else error.kind.value,
                status_code=None,
                stop_reason=response.stop_reason,
                refusal_category=response.refusal_category,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                thinking_tokens=usage.thinking_tokens,
                cost_usd=self._costs.cost(response.model, usage),
                latency_ms=response.latency_ms,
                provider_request_id=response.request_id,
                output_hash=sha256(response.text) if error is None else None,
            )
        )
