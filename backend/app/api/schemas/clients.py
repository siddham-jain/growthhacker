from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl

from app.api import ids
from app.api.schemas.common import ApiModel, Market, WindowDays
from app.api.schemas.runs import RunOut
from app.domain.runs import ClientLifecycle
from app.models.tenancy import Client
from app.models.youtube_cache import YtChannel
from app.pipeline.client_settings import read_settings


class ChannelRef(BaseModel):
    channel_id: str
    title: str | None = None
    handle: str | None = None
    thumbnail_url: str | None = None
    subscriber_count: int | None = None
    verified: bool
    verified_by: Literal["website_link", "channel_link", "oauth", "manual"] | None = None


class ClientCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    website_url: HttpUrl | None = None
    channel_ref: str | None = Field(default=None, min_length=2, max_length=300)
    # without a market, or with market_explicit false (a browser locale), onboarding infers it from the site
    market: Market | None = None
    market_explicit: bool = True
    window_days: WindowDays = 30
    # with neither website nor channel, onboarding locates the business from its name (03 §14)
    start_onboarding: bool = True


class ClientOut(BaseModel):
    id: str
    name: str
    website_url: str | None
    own_channel: ChannelRef | None
    lifecycle: ClientLifecycle
    window_days: WindowDays
    market: Market
    refresh_hour_local: int
    paused: bool
    oauth_connected: bool
    active_run_id: str | None
    last_success_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    version: int

    @classmethod
    def from_row(
        cls,
        client: Client,
        *,
        oauth_connected: bool,
        active_run_id: str | None,
        last_success_at: AwareDatetime | None,
        own_channel: YtChannel | None = None,
    ) -> "ClientOut":
        settings = read_settings(client)
        channel = None
        if client.own_channel_id is not None:
            channel = ChannelRef(
                channel_id=client.own_channel_id,
                title=own_channel.title if own_channel else None,
                handle=own_channel.handle if own_channel else None,
                thumbnail_url=own_channel.thumbnail_url if own_channel else None,
                subscriber_count=None
                if own_channel is None or own_channel.hidden_subscriber_count
                else own_channel.subscriber_count,
                verified=client.own_channel_status == "verified",
                verified_by=_verified_by(client),
            )
        return cls(
            id=ids.encode(ids.CLIENT, client.id),
            name=client.name,
            website_url=client.website_url,
            own_channel=channel,
            lifecycle=ClientLifecycle(client.lifecycle),
            window_days=settings.window_days,
            market=Market.model_construct(
                country=client.country or "", language=client.language or "", timezone=client.timezone
            ),
            refresh_hour_local=settings.refresh_hour_local,
            paused=client.lifecycle == ClientLifecycle.paused.value,
            oauth_connected=oauth_connected,
            active_run_id=active_run_id,
            last_success_at=last_success_at,
            created_at=client.created_at,
            updated_at=client.updated_at,
            version=settings.version,
        )


def _verified_by(client: Client) -> str | None:
    for entry in client.own_channel_verification or []:
        method = entry.get("verified_by") if isinstance(entry, dict) else None
        if method in {"website_link", "channel_link", "oauth", "manual"}:
            return method
    return None


class ClientCreatedOut(BaseModel):
    client: ClientOut
    onboarding_run: RunOut | None = None
    onboarding_unavailable: str | None = None


class ClientPatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    window_days: WindowDays | None = None
    market: Market | None = None
    refresh_hour_local: int | None = Field(default=None, ge=0, le=23)
    paused: bool | None = None


class ClientUpdateOut(BaseModel):
    client: ClientOut
    cards_run: RunOut | None = None


class OnboardingIn(ApiModel):
    website_url: HttpUrl | None = None
    channel_ref: str | None = Field(default=None, min_length=2, max_length=300)


class DeletingOut(BaseModel):
    status: Literal["deleting"] = "deleting"
