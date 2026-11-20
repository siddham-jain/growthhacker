from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel

from app.ports.llm import (
    CallBudget,
    CallContext,
    LLMClient,
    LLMError,
    PayloadClass,
    PromptInput,
    TaskId,
)
from app.services.website_intel.profile.prompts import prompt_ref


@dataclass
class LlmUsage:
    calls: int = 0
    failures: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    by_task: Counter[str] = field(default_factory=Counter)
    models: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_task": dict(self.by_task),
            "models": self.models,
            "errors": self.errors[:10],
        }


class LlmGateway:
    """the only place this component calls the llm port; every failure returns None for a deterministic degrade."""

    def __init__(
        self,
        client: LLMClient | None,
        *,
        client_id: UUID | None,
        run_id: UUID | None,
        stage: str = "build_profile",
        cost_cap_usd: float | None = None,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._run_id = run_id
        self._stage = stage
        self._cost_cap_usd = cost_cap_usd
        self.usage = LlmUsage()

    @property
    def available(self) -> bool:
        return self._client is not None

    async def structured[T: BaseModel](
        self,
        task: TaskId,
        schema: type[T],
        prompt: PromptInput,
        *,
        payload: frozenset[PayloadClass] = frozenset({PayloadClass.public_web}),
        max_output_tokens: int | None = None,
        idempotency_key: str | None = None,
        prompt_id: str | None = None,
    ) -> T | None:
        if self._client is None:
            return None
        self.usage.calls += 1
        self.usage.by_task[task.value] += 1
        try:
            result = await self._client.generate_structured(
                task=task,
                schema=schema,
                system=prompt_ref(prompt_id or task.value),
                input=prompt,
                context=CallContext(
                    run_id=self._run_id,
                    client_id=self._client_id,
                    stage=self._stage,
                    payload_classes=payload,
                    idempotency_key=idempotency_key,
                ),
                budget=self._budget(max_output_tokens),
            )
        except LLMError as error:
            self.usage.failures += 1
            self.usage.errors.append(f"{task.value}: {error.kind.value}: {str(error.detail)[:200]}")
            return None
        self.usage.cost_usd += result.cost_usd
        self.usage.input_tokens += result.usage.input_tokens
        self.usage.output_tokens += result.usage.output_tokens
        self.usage.models[task.value] = result.model
        return result.value

    def _budget(self, max_output_tokens: int | None) -> CallBudget | None:
        # the client refuses a call whose worst-case estimate exceeds what is left of the stage's budget
        headroom = None if self._cost_cap_usd is None else max(self._cost_cap_usd - self.usage.cost_usd, 0.0)
        if max_output_tokens is None and headroom is None:
            return None
        return CallBudget(max_output_tokens=max_output_tokens, max_cost_usd=headroom)
