import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.adapters.llm.providers.anthropic_provider import AnthropicProvider
from app.adapters.llm.providers.openai_compat import OpenAICompatibleProvider
from app.adapters.llm.schema_compiler import compile_schema
from app.adapters.llm.types import ProviderRequest, TextBlock, Turn
from app.ports.llm import LLMError, LLMErrorKind
from app.services.llm.writer.schemas import CardCopyLLM
from tests.llm.factories import make_llm_settings

SAMPLING = {"temperature", "top_p", "top_k"}


class Recorder:
    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.paths.append(request.url.path)
        return httpx2.Response(
            self.status, json=self.body, headers={"request-id": "req_test", **self.headers}
        )


def request(model: str, *, schema: bool = True, effort: Any = None) -> ProviderRequest:
    return ProviderRequest(
        model=model,
        system_blocks=(TextBlock("static rules", cacheable=True),),
        user_blocks=(TextBlock("client context", cacheable=True), TextBlock("evidence")),
        followups=(Turn("assistant", "{}"), Turn("user", "fix it")),
        output_schema=compile_schema(CardCopyLLM) if schema else None,
        max_output_tokens=1024,
        timeout_s=10,
        effort=effort,
        metadata_user_id="opaque",
    )


def message(content: list[dict[str, Any]], stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 0,
            "output_tokens_details": {"thinking_tokens": 12},
        },
        **extra,
    }


def anthropic(recorder: Callable[[httpx2.Request], httpx2.Response]) -> AnthropicProvider:
    settings = make_llm_settings().anthropic
    return AnthropicProvider(
        settings, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    )


async def test_anthropic_request_shape_for_haiku_has_no_effort_or_sampling() -> None:
    recorder = Recorder(200, message([{"type": "text", "text": "{}"}]))
    await anthropic(recorder).complete(request("claude-haiku-4-5", effort="low"))
    body = recorder.requests[0]
    assert recorder.paths == ["/v1/messages"]
    assert not SAMPLING & set(body)
    assert "tools" not in body and "tool_choice" not in body
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": json.loads(compile_schema(CardCopyLLM).canonical_json)}
    }
    assert body["system"] == [
        {"type": "text", "text": "static rules", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["messages"][0]["content"][1]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["metadata"] == {"user_id": "opaque"}


async def test_anthropic_extra_params_apply_without_sampling_or_overrides() -> None:
    recorder = Recorder(200, message([{"type": "text", "text": "{}"}]))
    settings = make_llm_settings().anthropic.model_copy(
        update={"extra_params": {"service_tier": "auto", "temperature": 0.2, "top_p": 0.9, "model": "other"}}
    )
    provider = AnthropicProvider(
        settings, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    )
    await provider.complete(request("claude-haiku-4-5"))
    body = recorder.requests[0]
    assert body["service_tier"] == "auto"
    assert not SAMPLING & set(body)
    assert body["model"] == "claude-haiku-4-5"


async def test_anthropic_sonnet_gets_explicit_effort() -> None:
    recorder = Recorder(200, message([{"type": "text", "text": "{}"}]))
    await anthropic(recorder).complete(request("claude-sonnet-5-5", effort="low"))
    assert recorder.requests[0]["output_config"]["effort"] == "low"
    assert "thinking" not in recorder.requests[0]


async def test_anthropic_reads_text_after_thinking_blocks() -> None:
    body = message(
        [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": "1}"},
        ]
    )
    response = await anthropic(Recorder(200, body)).complete(request("claude-sonnet-5-5"))
    assert response.text == '{"a":1}'
    assert response.stop_reason == "end_turn"
    assert response.usage.cache_write_tokens == 2000
    assert response.usage.thinking_tokens == 12
    assert response.model == "claude-haiku-4-5-20251001"
    assert response.request_id == "req_test"


async def test_anthropic_refusal_with_null_details_discards_text() -> None:
    body = message([{"type": "text", "text": "partial"}], stop_reason="refusal", stop_details=None)
    response = await anthropic(Recorder(200, body)).complete(request("claude-sonnet-5-5"))
    assert response.stop_reason == "refusal"
    assert response.text == ""
    assert response.refusal_category is None


async def test_anthropic_refusal_category_is_recorded() -> None:
    body = message(
        [], stop_reason="refusal", stop_details={"type": "refusal", "category": "bio", "explanation": None}
    )
    response = await anthropic(Recorder(200, body)).complete(request("claude-sonnet-5-5"))
    assert response.refusal_category == "bio"


async def test_anthropic_max_tokens_is_reported() -> None:
    response = await anthropic(
        Recorder(200, message([{"type": "text", "text": "{"}], "max_tokens"))
    ).complete(request("claude-haiku-4-5"))
    assert response.stop_reason == "max_tokens"


def error_body(kind: str, message_text: str, **error: Any) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message_text, **error}}


@pytest.mark.parametrize(
    ("status", "body", "headers", "kind", "retry_after"),
    [
        (
            429,
            error_body("rate_limit_error", "slow down"),
            {"retry-after": "2"},
            LLMErrorKind.rate_limited,
            2.0,
        ),
        (429, error_body("rate_limit_error", "slow down"), {}, LLMErrorKind.rate_limited, None),
        (
            429,
            error_body(
                "rate_limit_error", "spend limit", details={"error_code": "enforced_spend_limit_reached"}
            ),
            {},
            LLMErrorKind.spend_cap,
            None,
        ),
        (529, error_body("overloaded_error", "overloaded"), {}, LLMErrorKind.overloaded, None),
        (500, error_body("api_error", "boom"), {}, LLMErrorKind.server_error, None),
        (401, error_body("authentication_error", "bad key"), {}, LLMErrorKind.auth, None),
        (402, error_body("billing_error", "billing"), {}, LLMErrorKind.billing, None),
        (403, error_body("permission_error", "nope"), {}, LLMErrorKind.permission, None),
        (404, error_body("not_found_error", "model: claude-nope"), {}, LLMErrorKind.model_not_found, None),
        (
            400,
            error_body("invalid_request_error", "prompt is too long: 300000 tokens"),
            {},
            LLMErrorKind.context_too_long,
            None,
        ),
        (413, error_body("request_too_large", "too large"), {}, LLMErrorKind.request_too_large, None),
        (
            400,
            error_body("invalid_request_error", "temperature is deprecated"),
            {},
            LLMErrorKind.bad_request,
            None,
        ),
    ],
)
async def test_anthropic_errors_are_classified(
    status: int, body: dict[str, Any], headers: dict[str, str], kind: LLMErrorKind, retry_after: float | None
) -> None:
    with pytest.raises(LLMError) as raised:
        await anthropic(Recorder(status, body, headers)).complete(request("claude-haiku-4-5"))
    assert raised.value.kind is kind
    assert raised.value.retry_after_s == retry_after
    assert raised.value.status_code == status


async def test_anthropic_connection_errors_are_classified() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("dns failure", request=request)

    with pytest.raises(LLMError) as raised:
        await anthropic(broken).complete(request("claude-haiku-4-5"))
    assert raised.value.kind is LLMErrorKind.connection


def completion(content: str | None, finish: str = "stop", **message_extra: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gw-medium-2026",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content, **message_extra},
            }
        ],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {"cached_tokens": 600},
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    }


def gateway(recorder: Recorder, **overrides: Any) -> OpenAICompatibleProvider:
    settings = make_llm_settings(
        openai_compat={
            "api_key": "k",
            "base_url": "http://gateway.test/v1",
            "low": {"model": "gw-low"},
            "medium": {"model": "gw-medium"},
            "high": {"model": "gw-high"},
            **overrides,
        }
    ).openai_compat
    return OpenAICompatibleProvider(
        settings, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    )


async def test_gateway_request_uses_strict_schema_and_configured_token_param() -> None:
    recorder = Recorder(200, completion("{}"))
    response = await gateway(recorder, extra_body={"provider": {"require_parameters": True}}).complete(
        request("gw-medium", effort="low")
    )
    body = recorder.requests[0]
    assert recorder.paths == ["/v1/chat/completions"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["max_completion_tokens"] == 1024
    assert "reasoning_effort" not in body
    assert not SAMPLING & set(body)
    assert body["provider"] == {"require_parameters": True}
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert response.usage.input_tokens == 400
    assert response.usage.cache_read_tokens == 600
    assert response.model == "gw-medium-2026"


async def test_gateway_optional_params_only_when_configured() -> None:
    recorder = Recorder(200, completion("{}"))
    provider = gateway(
        recorder, token_param="max_tokens", supports_reasoning_effort=True, extra_params={"temperature": 0}
    )
    await provider.complete(request("gw-medium", effort="medium"))
    body = recorder.requests[0]
    assert body["max_tokens"] == 1024 and "max_completion_tokens" not in body
    assert body["reasoning_effort"] == "medium"
    assert body["temperature"] == 0


@pytest.mark.parametrize(
    ("body", "stop_reason", "text"),
    [
        (completion("{}", "length"), "max_tokens", "{}"),
        (completion(None, "content_filter"), "content_filter", ""),
        (completion(None, "stop", refusal="I can't help with that"), "refusal", ""),
        (completion(None), "end_turn", ""),
    ],
)
async def test_gateway_finish_reasons(body: dict[str, Any], stop_reason: str, text: str) -> None:
    response = await gateway(Recorder(200, body)).complete(request("gw-low"))
    assert response.stop_reason == stop_reason
    assert response.text == text


async def test_prompt_only_dialect_extracts_json_from_prose() -> None:
    provider = gateway(Recorder(200, completion("x")), structured_dialect="prompt_only")
    assert provider.schema_in_prompt()
    assert provider.extract_json('Sure! Here it is: {"a": {"b": "}"}} hope that helps') == {"a": {"b": "}"}}


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (
            429,
            {"error": {"message": "quota", "type": "insufficient_quota", "code": "insufficient_quota"}},
            LLMErrorKind.spend_cap,
        ),
        (
            400,
            {"error": {"message": "too long", "code": "context_length_exceeded"}},
            LLMErrorKind.context_too_long,
        ),
        (
            400,
            {
                "error": {
                    "message": "The response was filtered due to content_filter",
                    "code": "content_filter",
                }
            },
            LLMErrorKind.refusal,
        ),
        (503, {"error": {"message": "unavailable"}}, LLMErrorKind.server_error),
    ],
)
async def test_gateway_errors_are_classified(status: int, body: dict[str, Any], kind: LLMErrorKind) -> None:
    with pytest.raises(LLMError) as raised:
        await gateway(Recorder(status, body)).complete(request("gw-low"))
    assert raised.value.kind is kind
