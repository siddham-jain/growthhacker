import random
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import LLMResilienceSettings
from app.ports.llm import LLMError, LLMErrorKind


class Action(StrEnum):
    retry = "retry"
    shrink = "shrink"
    next_route = "next_route"
    next_model = "next_model"


class CircuitEffect(StrEnum):
    none = "none"
    failure = "failure"
    open_spend_cap = "open_spend_cap"
    hard_open_provider = "hard_open_provider"
    hard_open_route = "hard_open_route"


@dataclass(frozen=True, slots=True)
class Rule:
    action: Action
    circuit: CircuitEffect


RULES: dict[LLMErrorKind, Rule] = {
    LLMErrorKind.rate_limited: Rule(Action.retry, CircuitEffect.failure),
    LLMErrorKind.spend_cap: Rule(Action.next_route, CircuitEffect.open_spend_cap),
    LLMErrorKind.overloaded: Rule(Action.retry, CircuitEffect.failure),
    LLMErrorKind.server_error: Rule(Action.retry, CircuitEffect.failure),
    LLMErrorKind.timeout: Rule(Action.retry, CircuitEffect.failure),
    LLMErrorKind.connection: Rule(Action.retry, CircuitEffect.failure),
    LLMErrorKind.auth: Rule(Action.next_route, CircuitEffect.hard_open_provider),
    LLMErrorKind.permission: Rule(Action.next_route, CircuitEffect.hard_open_route),
    LLMErrorKind.billing: Rule(Action.next_route, CircuitEffect.open_spend_cap),
    LLMErrorKind.model_not_found: Rule(Action.next_route, CircuitEffect.hard_open_route),
    LLMErrorKind.context_too_long: Rule(Action.shrink, CircuitEffect.none),
    LLMErrorKind.request_too_large: Rule(Action.shrink, CircuitEffect.none),
    LLMErrorKind.bad_request: Rule(Action.next_route, CircuitEffect.failure),
    LLMErrorKind.refusal: Rule(Action.next_model, CircuitEffect.none),
}
DEFAULT_RULE = Rule(Action.next_route, CircuitEffect.failure)


class RetryPolicy:
    def __init__(self, settings: LLMResilienceSettings, *, jitter: random.Random | None = None) -> None:
        self._settings = settings
        self._jitter = jitter or random.Random()

    def rule(self, error: LLMError) -> Rule:
        return RULES.get(error.kind, DEFAULT_RULE)

    def retry_delay(
        self, error: LLMError, retry_number: int, *, remaining_s: float | None, timeout_s: float
    ) -> float | None:
        # none means do not retry on the same route
        settings = self._settings
        if error.kind is LLMErrorKind.rate_limited:
            if error.retry_after_s is None or error.retry_after_s > settings.rate_limit_max_wait_s:
                return None
            delay = error.retry_after_s
        else:
            backoff = min(settings.retry_cap_s, settings.retry_base_s * 2**retry_number)
            delay = backoff + self._jitter.uniform(0, settings.retry_base_s)
        if remaining_s is not None:
            if error.kind is LLMErrorKind.timeout and remaining_s - delay < timeout_s:
                return None
            if remaining_s - delay < settings.min_viable_s:
                return None
        return delay
