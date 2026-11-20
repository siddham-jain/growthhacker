from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.tenancy import Client


class ClientSettings(BaseModel):
    """the run-relevant keys of clients.settings; unknown keys from other components pass through."""

    model_config = ConfigDict(extra="allow")

    window_days: Literal[7, 30, 90] = 30
    refresh_hour_local: int = Field(default=6, ge=0, le=23)
    auto_confirm_setup: bool = False
    # bumped by edits that change run inputs (window, market); feeds RunConfigSnapshot.config_version
    config_version: int = Field(default=1, ge=1)
    # bumped by every PATCH; the If-Match / ETag value for the client resource
    version: int = Field(default=1, ge=1)
    paused_from: str | None = None
    # true once the user picked the market; a browser locale or onboarding's inference never counts
    market_explicit: bool = False


def read_settings(client: Client) -> ClientSettings:
    return ClientSettings.model_validate(client.settings or {})


def write_settings(client: Client, settings: ClientSettings) -> None:
    client.settings = settings.model_dump(mode="json")


def merged(settings: ClientSettings, **changes: Any) -> ClientSettings:
    return ClientSettings.model_validate({**settings.model_dump(mode="json"), **changes})
