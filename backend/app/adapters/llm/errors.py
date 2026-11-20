from typing import Any

import anthropic
import openai

from app.ports.llm import LLMError, LLMErrorKind

CONTEXT_MESSAGES = ("prompt is too long", "context length", "context_length_exceeded", "maximum context")


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _body_error(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            return error
    return {}


def _status_kind(status: int, message: str, error_code: str | None) -> LLMErrorKind:
    lowered = message.lower()
    if status == 400 and any(marker in lowered for marker in CONTEXT_MESSAGES):
        return LLMErrorKind.context_too_long
    if status == 400 and ("content_filter" in lowered or "content management policy" in lowered):
        return LLMErrorKind.refusal
    by_status = {
        401: LLMErrorKind.auth,
        402: LLMErrorKind.billing,
        403: LLMErrorKind.permission,
        404: LLMErrorKind.model_not_found,
        408: LLMErrorKind.timeout,
        413: LLMErrorKind.request_too_large,
        429: LLMErrorKind.rate_limited,
        529: LLMErrorKind.overloaded,
    }
    if status == 429 and error_code in {
        "enforced_spend_limit_reached",
        "contract_spend_limit_reached",
        "insufficient_quota",
    }:
        return LLMErrorKind.spend_cap
    if status in by_status:
        return by_status[status]
    if status >= 500:
        return LLMErrorKind.server_error
    return LLMErrorKind.bad_request


def classify_anthropic_error(exc: Exception, *, model: str) -> LLMError:
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError(LLMErrorKind.timeout, "request timed out", provider="anthropic", model=model)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError(LLMErrorKind.connection, "connection failed", provider="anthropic", model=model)
    if isinstance(exc, anthropic.APIStatusError):
        error = _body_error(exc.body)
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        code = details.get("error_code") or error.get("code")
        if isinstance(
            exc, anthropic.EnforcedSpendLimitReachedError | anthropic.ContractSpendLimitReachedError
        ):
            code = "enforced_spend_limit_reached"
        message = str(error.get("message") or exc.message)
        kind = _status_kind(exc.status_code, message, code)
        return LLMError(
            kind,
            message[:300],
            provider="anthropic",
            model=model,
            status_code=exc.status_code,
            request_id=exc.request_id,
            retry_after_s=_retry_after(exc.response.headers),
        )
    raise exc


def classify_openai_error(exc: Exception, *, model: str) -> LLMError:
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(LLMErrorKind.timeout, "request timed out", provider="openai_compat", model=model)
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(LLMErrorKind.connection, "connection failed", provider="openai_compat", model=model)
    if isinstance(exc, openai.APIStatusError):
        error = _body_error(exc.body)
        code = error.get("code") or error.get("type")
        message = str(error.get("message") or exc.message)
        if code == "context_length_exceeded":
            message = f"context_length_exceeded: {message}"
        kind = _status_kind(exc.status_code, message, code if isinstance(code, str) else None)
        return LLMError(
            kind,
            message[:300],
            provider="openai_compat",
            model=model,
            status_code=exc.status_code,
            request_id=exc.request_id,
            retry_after_s=_retry_after(exc.response.headers),
        )
    raise exc
