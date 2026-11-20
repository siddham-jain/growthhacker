import json
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.adapters.llm.capabilities import ModelCapabilities, anthropic_capabilities
from app.adapters.llm.fallback_chain import sha256
from app.adapters.llm.types import ProviderRequest, ProviderResponse, StopReason
from app.core.config import Tier
from app.ports.llm import (
    CallBudget,
    CallContext,
    LLMBudgetExceeded,
    LLMClient,
    LLMError,
    LLMResult,
    LLMUnavailable,
    PromptInput,
    PromptRef,
    TaskId,
    TokenUsage,
)
from app.services.llm.prompts import PromptRegistry


@dataclass(frozen=True, slots=True)
class FakeCall:
    method: str
    task: TaskId
    schema: type[BaseModel] | None
    system: PromptRef
    input: PromptInput
    context: CallContext
    tier: Tier | None


# a scripted reply: a pydantic value, a dict, a string, an LLMError to raise, or a callable producing one
type FakeReply = BaseModel | dict[str, Any] | str | LLMError | Callable[[FakeCall], Any]


class FakeLLMClient:
    """deterministic LLMClient for other components' tests; records calls and can inject any error kind."""

    def __init__(
        self,
        replies: Mapping[TaskId, FakeReply | list[FakeReply]] | None = None,
        *,
        default: Callable[[FakeCall], Any] | None = None,
        model: str = "fake-model",
    ) -> None:
        self.calls: list[FakeCall] = []
        self._queues: dict[TaskId, deque[FakeReply]] = {}
        self._sticky: dict[TaskId, FakeReply] = {}
        self._default = default
        self._model = model
        for task, reply in (replies or {}).items():
            if isinstance(reply, list):
                self.queue(task, *reply)
            else:
                self._sticky[task] = reply

    def queue(self, task: TaskId, *replies: FakeReply) -> None:
        self._queues.setdefault(task, deque()).extend(replies)

    def calls_for(self, task: TaskId) -> list[FakeCall]:
        return [call for call in self.calls if call.task is task]

    def _reply(self, call: FakeCall) -> Any:
        queue = self._queues.get(call.task)
        if queue:
            reply: Any = queue.popleft()
        elif call.task in self._sticky:
            reply = self._sticky[call.task]
        elif self._default is not None:
            reply = self._default
        else:
            raise AssertionError(f"FakeLLMClient has no reply scripted for task {call.task.value}")
        if callable(reply) and not isinstance(reply, BaseModel):
            reply = reply(call)
        if isinstance(reply, LLMUnavailable | LLMBudgetExceeded):
            raise reply
        if isinstance(reply, LLMError):
            raise LLMUnavailable([reply])
        return reply

    def _result(self, call: FakeCall, value: Any, raw_text: str) -> LLMResult[Any]:
        return LLMResult[Any](
            value=value,
            raw_text=raw_text,
            task=call.task,
            prompt_id=call.system.prompt_id,
            prompt_version="fake",
            provider="fake",
            model=self._model,
            route_index=0,
            attempts=1,
            repaired=False,
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=0,
            stop_reason="end_turn",
        )

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
        call = FakeCall("structured", task, schema, system, input, context, tier)
        self.calls.append(call)
        reply = self._reply(call)
        value = reply if isinstance(reply, schema) else schema.model_validate(reply)
        return self._result(call, value, value.model_dump_json())

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
        call = FakeCall("text", task, None, system, input, context, tier)
        self.calls.append(call)
        reply = self._reply(call)
        if not isinstance(reply, str):
            raise AssertionError("generate_text replies must be strings")
        return self._result(call, reply, reply)


def fake_response(
    text: str,
    *,
    stop_reason: StopReason = "end_turn",
    model: str = "claude-haiku-4-5-20251001",
    usage: TokenUsage | None = None,
    refusal_category: str | None = None,
) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        stop_reason=stop_reason,
        usage=usage or TokenUsage(input_tokens=100, output_tokens=20),
        model=model,
        request_id="req_fake",
        latency_ms=5,
        raw_finish_reason=stop_reason,
        refusal_category=refusal_category,
    )


type ProviderStep = ProviderResponse | LLMError | Callable[[ProviderRequest], ProviderResponse]


class FakeProvider:
    """scripted ChatProvider for fallback-chain tests."""

    def __init__(
        self,
        name: str,
        script: list[ProviderStep],
        *,
        capabilities: ModelCapabilities | None = None,
        schema_in_prompt: bool = False,
    ) -> None:
        self.name = name
        self.requests: list[ProviderRequest] = []
        self._script = deque(script)
        self._capabilities = capabilities
        self._schema_in_prompt = schema_in_prompt

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities or anthropic_capabilities(model)

    def schema_in_prompt(self) -> bool:
        return self._schema_in_prompt

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"FakeProvider {self.name} has no scripted step left")
        step = self._script.popleft()
        if isinstance(step, LLMError):
            raise step
        if callable(step):
            return step(request)
        return step

    def extract_json(self, text: str) -> Any:
        return json.loads(text)

    async def aclose(self) -> None:
        return None


def fixture_path(
    directory: Path, prompt_id: str, prompt_version: str, prompt_input: PromptInput, schema: str
) -> Path:
    digest = sha256(f"{schema}\x1f{prompt_input.model_dump_json()}")
    return directory / prompt_id / prompt_version / f"{digest}.json"


class RecordingLLMClient:
    """wraps a real client and stores validated outputs as replay fixtures (synthetic inputs only)."""

    def __init__(self, inner: LLMClient, directory: Path) -> None:
        self._inner = inner
        self._directory = directory

    def _write(self, path: Path, result: LLMResult[Any], value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"value": value, "model": result.model, "usage": result.usage.model_dump()}
        path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf-8")

    async def generate_structured[T: BaseModel](self, *, schema: type[T], **kwargs: Any) -> LLMResult[T]:
        result = await self._inner.generate_structured(schema=schema, **kwargs)
        path = fixture_path(
            self._directory, result.prompt_id, result.prompt_version, kwargs["input"], schema.__name__
        )
        self._write(path, result, result.value.model_dump(mode="json"))
        return result

    async def generate_text(self, **kwargs: Any) -> LLMResult[str]:
        result = await self._inner.generate_text(**kwargs)
        path = fixture_path(self._directory, result.prompt_id, result.prompt_version, kwargs["input"], "text")
        self._write(path, result, result.value)
        return result


class ReplayLLMClient:
    """serves recorded fixtures; a miss fails loudly so stale prompts cannot replay silently."""

    def __init__(self, directory: Path, prompts: PromptRegistry) -> None:
        self._directory = directory
        self._prompts = prompts

    def _load(self, system: PromptRef, prompt_input: PromptInput, schema: str) -> tuple[str, dict[str, Any]]:
        version = self._prompts.resolve(system).prompt_version
        path = fixture_path(self._directory, system.prompt_id, version, prompt_input, schema)
        if not path.is_file():
            raise LookupError(
                f"no recorded fixture for {system.prompt_id} {version} at {path}; re-record it with real keys"
            )
        return version, json.loads(path.read_text(encoding="utf-8"))

    def _result(
        self, task: TaskId, system: PromptRef, version: str, value: Any, payload: dict[str, Any]
    ) -> Any:
        return LLMResult[Any](
            value=value,
            raw_text="",
            task=task,
            prompt_id=system.prompt_id,
            prompt_version=version,
            provider="replay",
            model=payload["model"],
            route_index=0,
            attempts=0,
            repaired=False,
            usage=TokenUsage.model_validate(payload["usage"]),
            cost_usd=0.0,
            latency_ms=0,
            stop_reason="replay",
            cache_hit=True,
        )

    async def generate_structured[T: BaseModel](
        self, *, task: TaskId, schema: type[T], system: PromptRef, input: PromptInput, **_: Any
    ) -> LLMResult[T]:
        version, payload = self._load(system, input, schema.__name__)
        return self._result(task, system, version, schema.model_validate(payload["value"]), payload)

    async def generate_text(
        self, *, task: TaskId, system: PromptRef, input: PromptInput, **_: Any
    ) -> LLMResult[str]:
        version, payload = self._load(system, input, "text")
        return self._result(task, system, version, payload["value"], payload)
