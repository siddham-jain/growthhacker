import json
import time
from typing import Any

import anthropic
import httpx2

from app.adapters.llm.capabilities import ModelCapabilities, anthropic_capabilities, supported_effort
from app.adapters.llm.errors import classify_anthropic_error
from app.adapters.llm.types import ProviderRequest, ProviderResponse, StopReason, TextBlock
from app.core.config import LLMProviderSettings
from app.ports.llm import TokenUsage

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "stop_sequence": "stop_sequence",
}
EPHEMERAL = {"type": "ephemeral"}
# anthropic routes 400 on sampling params (D18), so workspace extra params never carry them
SAMPLING_PARAMS = frozenset({"temperature", "top_p", "top_k"})


def _text_param(block: TextBlock) -> dict[str, Any]:
    param: dict[str, Any] = {"type": "text", "text": block.text}
    if block.cacheable:
        param["cache_control"] = EPHEMERAL
    return param


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, settings: LLMProviderSettings, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        if settings.api_key is None:
            raise ValueError("the anthropic provider needs settings.llm.anthropic.api_key")
        self._connect_timeout_s = settings.connect_timeout_s
        self._extra_params = {
            key: value for key, value in settings.extra_params.items() if key not in SAMPLING_PARAMS
        }
        # the fallback chain owns retries so every attempt is accounted and deadline-aware
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            max_retries=0,
            http_client=http_client,
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return anthropic_capabilities(model)

    def schema_in_prompt(self) -> bool:
        return False

    def build_params(self, request: ProviderRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [_text_param(block) for block in request.user_blocks]}
        ]
        messages.extend({"role": turn.role, "content": turn.text} for turn in request.followups)
        params: dict[str, Any] = {
            **self._extra_params,
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": [_text_param(block) for block in request.system_blocks],
            "messages": messages,
        }
        output_config: dict[str, Any] = {}
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": dict(request.output_schema.schema)}
        effort = supported_effort(self.capabilities(request.model), request.effort)
        if effort is not None:
            output_config["effort"] = effort
        if output_config:
            params["output_config"] = output_config
        if request.metadata_user_id is not None:
            params["metadata"] = {"user_id": request.metadata_user_id}
        return params

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.monotonic()
        try:
            message = await self._client.messages.create(
                **self.build_params(request),
                timeout=anthropic.Timeout(request.timeout_s, connect=self._connect_timeout_s),
            )
        except anthropic.APIError as exc:
            raise classify_anthropic_error(exc, model=request.model) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        raw_stop = message.stop_reason or "other"
        stop_reason = STOP_REASONS.get(raw_stop, "other")
        category = None
        if stop_reason == "refusal":
            details = message.stop_details
            category = getattr(details, "category", None) if details is not None else None
        # thinking blocks can precede text, so read by block type; refusals discard partial output
        text = (
            ""
            if stop_reason == "refusal"
            else "".join(block.text for block in message.content if block.type == "text")
        )
        usage = message.usage
        details = usage.output_tokens_details
        return ProviderResponse(
            text=text,
            stop_reason=stop_reason,
            refusal_category=category,
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                output_tokens=usage.output_tokens,
                thinking_tokens=details.thinking_tokens if details is not None else None,
            ),
            model=message.model,
            request_id=message._request_id,
            latency_ms=latency_ms,
            raw_finish_reason=raw_stop,
        )

    def extract_json(self, text: str) -> Any:
        return json.loads(text)

    async def aclose(self) -> None:
        await self._client.close()
