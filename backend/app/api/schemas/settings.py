from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, SecretStr, field_validator

from app.api.schemas.common import ApiModel

ProviderName = Literal["anthropic", "openai_compat"]
_MODEL_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}$"


def _valid_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    local = value.startswith(("http://localhost", "http://127.0.0.1"))
    if not (value.startswith("https://") or local):
        raise ValueError("base_url must use https (plain http only for localhost)")
    return value.rstrip("/")


class TierModels(BaseModel):
    low: str | None = Field(default=None, pattern=_MODEL_ID)
    medium: str | None = Field(default=None, pattern=_MODEL_ID)
    high: str | None = Field(default=None, pattern=_MODEL_ID)


class TierModelsIn(ApiModel, TierModels):
    pass


class LLMProviderIn(ApiModel):
    base_url: str | None = Field(default=None, max_length=300)
    # write-only: stored encrypted, never returned by any endpoint
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=500)
    clear_api_key: bool = False
    models: TierModelsIn | None = None

    _base_url = field_validator("base_url")(_valid_base_url)


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


class LLMProviderOut(BaseModel):
    kind: Literal["anthropic", "openai_compatible"]
    base_url: str | None
    models: TierModels
    api_key_set: bool
    # where the active key comes from; the key itself is never returned
    api_key_source: Literal["workspace", "environment"] | None
    configured: bool


class LLMSettingsOut(BaseModel):
    enabled: bool
    provider_order: list[ProviderName]
    providers: dict[ProviderName, LLMProviderOut]
    updated_at: AwareDatetime | None = None
