import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, Field, SecretStr, field_validator

from app.api.schemas.common import ApiModel

ProviderName = Literal["anthropic", "openai_compat"]
TierName = Literal["low", "medium", "high"]
Effort = Literal["low", "medium", "high"]
StructuredDialect = Literal[
    "openai_json_schema_strict", "openai_json_schema", "openai_json_object", "prompt_only"
]
TokenParam = Literal["max_completion_tokens", "max_tokens"]
_MODEL_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$"
# request fields the adapter sets itself; extra params must not override them
RESERVED_PARAMS = frozenset(
    {
        "model",
        "messages",
        "system",
        "stream",
        "max_tokens",
        "max_completion_tokens",
        "response_format",
        "tools",
    }
)
MAX_EXTRA_BYTES = 4000


def _valid_base_url(value: str | None) -> str | None:
    # structure only; https and public-address rules need the deployment settings, so the service applies them
    if value is None:
        return None
    if any(char.isspace() or char == "\\" for char in value):
        raise ValueError("base_url must not contain spaces or backslashes")
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("base_url must be an absolute https URL")
    if "@" in parts.netloc:
        raise ValueError("base_url must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("base_url must not carry a query string or fragment")
    try:
        parts.port  # noqa: B018  (raises on an out-of-range port)
    except ValueError:
        raise ValueError("base_url has an invalid port") from None
    return value.rstrip("/")


def _valid_extra(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    reserved = sorted(RESERVED_PARAMS & set(value))
    if reserved:
        raise ValueError(f"these are set by GrowthHacker and cannot be overridden: {', '.join(reserved)}")
    if len(json.dumps(value)) > MAX_EXTRA_BYTES:
        raise ValueError(f"keep extra parameters under {MAX_EXTRA_BYTES} bytes of JSON")
    return value


class TierIn(ApiModel):
    model: str | None = Field(default=None, pattern=_MODEL_ID)
    max_tokens: int | None = Field(default=None, ge=256, le=64_000)
    timeout_s: float | None = Field(default=None, ge=5, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    effort: Effort | None = None


class TiersIn(ApiModel):
    low: TierIn | None = None
    medium: TierIn | None = None
    high: TierIn | None = None


class LLMProviderIn(ApiModel):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=300)
    # write-only: stored encrypted, never returned by any endpoint
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=500)
    clear_api_key: bool = False
    tiers: TiersIn | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    connect_timeout_s: float | None = Field(default=None, ge=1, le=60)
    # openai-compatible gateways only: passed verbatim to chat.completions.create
    extra_params: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    structured_dialect: StructuredDialect | None = None
    token_param: TokenParam | None = None
    supports_reasoning_effort: bool | None = None
    # drops every workspace override for this provider, keeping the key unless clear_api_key is sent
    reset: bool = False

    _base_url = field_validator("base_url")(_valid_base_url)
    _extra_params = field_validator("extra_params", "extra_body")(_valid_extra)


class LLMSettingsIn(ApiModel):
    enabled: bool | None = None
    provider_order: list[ProviderName] | None = Field(default=None, min_length=1, max_length=2)
    providers: dict[ProviderName, LLMProviderIn] = {}

    @field_validator("provider_order")
    @classmethod
    def _unique(cls, value: list[ProviderName] | None) -> list[ProviderName] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("provider_order must not repeat a provider")
        return value


class TierOut(BaseModel):
    model: str | None
    max_tokens: int
    timeout_s: float
    max_retries: int
    effort: Effort | None


class TiersOut(BaseModel):
    low: TierOut
    medium: TierOut
    high: TierOut


class OpenAICompatOptions(BaseModel):
    extra_params: dict[str, Any]
    extra_body: dict[str, Any]
    structured_dialect: StructuredDialect
    token_param: TokenParam
    supports_reasoning_effort: bool


class LLMProviderOut(BaseModel):
    kind: Literal["anthropic", "openai_compatible"]
    enabled: bool
    base_url: str | None
    tiers: TiersOut
    max_concurrency: int
    connect_timeout_s: float
    # null for anthropic, whose adapter takes no extra request parameters
    openai: OpenAICompatOptions | None
    api_key_set: bool
    # where the active key comes from; the key itself is never returned
    api_key_source: Literal["workspace", "environment"] | None
    configured: bool
    # fields this workspace overrides; everything else is the deployment default
    overridden: list[str]


class LLMSettingsOut(BaseModel):
    enabled: bool
    provider_order: list[ProviderName]
    providers: dict[ProviderName, LLMProviderOut]
    updated_at: AwareDatetime | None = None


class LLMTestIn(ApiModel):
    provider: ProviderName | None = None


class LLMRouteCheck(BaseModel):
    provider: ProviderName
    model: str | None
    status: Literal["ok", "degraded", "failed", "not_configured"]
    detail: str


class LLMTestOut(BaseModel):
    ok: bool
    checks: list[LLMRouteCheck]
    checked_at: AwareDatetime


class NotificationSettingsPatch(ApiModel):
    # null clears the address
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    first_brief_ready: bool | None = None
    daily_digest: bool | None = None
    news_closing_soon: bool | None = None


class NotificationSettingsOut(BaseModel):
    email: str | None
    first_brief_ready: bool
    daily_digest: bool
    news_closing_soon: bool
    # false until an email sender is configured; choices are saved either way
    delivery_enabled: bool
    updated_at: AwareDatetime | None = None
