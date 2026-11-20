import time
from typing import Any

import httpx2
import openai

from app.adapters.llm.capabilities import ModelCapabilities, openai_compat_capabilities, supported_effort
from app.adapters.llm.dialects import StructuredDialect, structured_dialects
from app.adapters.llm.errors import classify_openai_error
from app.adapters.llm.types import ProviderRequest, ProviderResponse, StopReason
from app.core.config import OpenAICompatSettings
from app.ports.llm import TokenUsage

FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


class OpenAICompatibleProvider:
    # one class serves openai, azure v1, openrouter, together, groq and vllm through base_url
    name = "openai_compat"

    def __init__(
        self, settings: OpenAICompatSettings, *, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        if settings.api_key is None or settings.base_url is None:
            raise ValueError("the openai-compatible provider needs llm.openai_compat.base_url and api_key")
        self._settings = settings
        self._dialect: StructuredDialect = structured_dialects.get(settings.structured_dialect)
        self._capabilities = openai_compat_capabilities(
            supports_reasoning_effort=settings.supports_reasoning_effort,
            context_window=settings.context_window,
            max_output_tokens=settings.max_output_tokens,
        )
        self._client = openai.AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            max_retries=0,
            default_headers=settings.extra_headers or None,
            http_client=http_client,
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities

    def schema_in_prompt(self) -> bool:
        return self._dialect.schema_in_prompt

    def build_params(self, request: ProviderRequest) -> dict[str, Any]:
        system_text = "\n\n".join(block.text for block in request.system_blocks)
        user_text = "\n\n".join(block.text for block in request.user_blocks)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]
        messages.extend({"role": turn.role, "content": turn.text} for turn in request.followups)
        params: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            self._settings.token_param: request.max_output_tokens,
            **self._settings.extra_params,
        }
        if request.output_schema is not None:
            response_format = self._dialect.response_format(request.output_schema)
            if response_format is not None:
                params["response_format"] = response_format
        effort = supported_effort(self._capabilities, request.effort)
        if effort is not None:
            params["reasoning_effort"] = effort
        if self._settings.extra_body:
            params["extra_body"] = dict(self._settings.extra_body)
        return params

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.monotonic()
        try:
            completion = await self._client.chat.completions.create(
                **self.build_params(request),
                timeout=openai.Timeout(request.timeout_s, connect=self._settings.connect_timeout_s),
            )
        except openai.APIError as exc:
            raise classify_openai_error(exc, model=request.model) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        choice = completion.choices[0] if completion.choices else None
        raw_finish = (choice.finish_reason if choice is not None else None) or "other"
        stop_reason = FINISH_REASONS.get(raw_finish, "other")
        message = choice.message if choice is not None else None
        refusal = getattr(message, "refusal", None) if message is not None else None
        if refusal:
            stop_reason = "refusal"
        text = (
            "" if message is None or message.tool_calls or stop_reason == "refusal" else message.content or ""
        )
        usage = completion.usage
        prompt_tokens = usage.prompt_tokens if usage is not None else 0
        cached = 0
        reasoning = None
        if usage is not None and usage.prompt_tokens_details is not None:
            cached = usage.prompt_tokens_details.cached_tokens or 0
        if usage is not None and usage.completion_tokens_details is not None:
            reasoning = usage.completion_tokens_details.reasoning_tokens
        return ProviderResponse(
            text=text,
            stop_reason=stop_reason,
            refusal_category="content_filter" if stop_reason in {"refusal", "content_filter"} else None,
            usage=TokenUsage(
                input_tokens=max(0, prompt_tokens - cached),
                cache_read_tokens=cached,
                output_tokens=usage.completion_tokens if usage is not None else 0,
                thinking_tokens=reasoning,
            ),
            model=completion.model or request.model,
            request_id=getattr(completion, "_request_id", None),
            latency_ms=latency_ms,
            raw_finish_reason=raw_finish,
        )

    def extract_json(self, text: str) -> Any:
        return self._dialect.extract(text)

    async def aclose(self) -> None:
        await self._client.close()
