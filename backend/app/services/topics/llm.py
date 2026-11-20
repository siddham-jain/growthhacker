from collections.abc import Sequence
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.ports.llm import (
    CallContext,
    LLMBudgetExceeded,
    LLMClient,
    LLMResult,
    PayloadClass,
    PromptInput,
    PromptRef,
    TaskId,
)

PAYLOAD = frozenset({PayloadClass.public_youtube, PayloadClass.client_profile})


class SpendGuard(Protocol):
    # run-level llm spend; allow() is checked before every call
    def allow(self, estimated_usd: float) -> bool: ...

    def record(self, cost_usd: float) -> None: ...


class UnlimitedSpend:
    def allow(self, estimated_usd: float) -> bool:
        return True

    def record(self, cost_usd: float) -> None:
        return None


def enum_of(values: Sequence[str]) -> Any:
    """a Literal over runtime values, so structured outputs can only pick ids we offered."""
    if not values:
        raise ValueError("an enum needs at least one value")
    return Literal[tuple(dict.fromkeys(values))]


async def call_structured[T: BaseModel](
    llm: LLMClient,
    *,
    task: TaskId,
    schema: type[T],
    prompt_id: str,
    prompt_input: PromptInput,
    client_id: UUID | None,
    run_id: UUID | None,
    stage: str,
    guard: SpendGuard,
    estimated_usd: float,
    idempotency_key: str | None,
) -> LLMResult[T]:
    if not guard.allow(estimated_usd):
        raise LLMBudgetExceeded(f"{task.value}: run llm budget exhausted")
    context = CallContext(
        run_id=run_id,
        client_id=client_id,
        stage=stage,
        payload_classes=PAYLOAD,
        idempotency_key=idempotency_key,
    )
    result = await llm.generate_structured(
        task=task,
        schema=schema,
        system=PromptRef(prompt_id=prompt_id),
        input=prompt_input,
        context=context,
        tier="low",
    )
    guard.record(result.cost_usd)
    return result
