import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.adapters.youtube.oauth_google import TOKEN_URL, GoogleOAuthFlow, InvalidGrant, TokenSet
from app.core.config import GoogleOAuthSettings, SecurityKey, YouTubeAnalyticsApiSettings
from app.core.security import SecretCipher
from app.domain.youtube import QuotaPriority
from app.models.own_analytics import OwnAnalyticsDaily, OwnSearchTerm, OwnTrafficSource, OwnVideoMetrics
from app.models.tenancy import Client, OAuthToken
from app.ports.youtube import AnalyticsQuery, AnalyticsTable
from app.services.youtube.context import request_context
from app.services.youtube.own_analytics import OAuthConnectionService, OwnAnalyticsService
from tests.foundation.factories import create_tenant
from tests.youtube.fixtures import CHANNEL_A, CHANNEL_B, NOW, video_id

pytestmark = pytest.mark.db

CTX = request_context(QuotaPriority.daily_refresh)


class FakeFlow:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.revoked: list[str] = []
        self.refresh_error: Exception | None = None

    def tokens(self, refresh: str | None = "refresh-1") -> TokenSet:
        return TokenSet(
            access_token=SecretStr("access-1"),
            refresh_token=SecretStr(refresh) if refresh else None,
            expires_at=self.clock.now() + timedelta(hours=1),
            scopes=["yt-analytics.readonly"],
            subject="google-sub-1",
        )

    def authorization_url(self, state: str, code_challenge: str) -> str:
        return f"https://accounts.example/auth?state={state}&challenge={code_challenge}"

    async def exchange(self, code: str, code_verifier: str) -> TokenSet:
        assert code == "code-1" and len(code_verifier) > 40
        return self.tokens()

    async def refresh(self, refresh_token: SecretStr) -> TokenSet:
        if self.refresh_error:
            raise self.refresh_error
        return self.tokens()

    async def revoke(self, token: SecretStr) -> bool:
        self.revoked.append(token.get_secret_value())
        return True


class FakeChannels:
    def __init__(self, channel_id: str | None) -> None:
        self.channel_id = channel_id

    async def own_channel_id(self, access_token, ctx):
        return self.channel_id


class FakeReports:
    def __init__(self) -> None:
        self.queries: list[AnalyticsQuery] = []

    async def report(self, access_token, query: AnalyticsQuery, ctx=None) -> AnalyticsTable:
        self.queries.append(query)
        dims = query.dimensions
        if dims == ["day"] and query.filters is None:
            # the api returns days only up to the last complete one, two to three days back
            days = [(query.end_date - timedelta(days=i)).isoformat() for i in (4, 3, 2)]
            return AnalyticsTable(
                column_headers=["day", *query.metrics],
                rows=[[day, 100, 80, 300, 45, 5, 1, 9, 2, 3] for day in days],
            )
        if dims == ["day"]:
            return AnalyticsTable(column_headers=["day", "views"], rows=[[query.start_date.isoformat(), 40]])
        if dims and dims[0] == "video":
            return AnalyticsTable(
                column_headers=["video", "creatorContentType", *query.metrics],
                rows=[[video_id(1), "SHORTS", 500, 400, 900, 30, 71.5, 12, 1, 20, 4, 2]],
            )
        if dims == ["insightTrafficSourceType"]:
            return AnalyticsTable(
                column_headers=["insightTrafficSourceType", "views", "estimatedMinutesWatched"],
                rows=[["YT_SEARCH", 200, 300], ["SHORTS", 100, 50]],
            )
        return AnalyticsTable(
            column_headers=["insightTrafficSourceDetail", "views", "estimatedMinutesWatched"], rows=[]
        )


@pytest.fixture
def cipher() -> SecretCipher:
    return SecretCipher([SecurityKey(kid="k1", key=SecretStr(SecretCipher.generate_key()))])


@pytest.fixture
async def tenant(session):
    return await create_tenant(session, "apollo")


def service(uow_factory, redis: Redis, redis_prefix: str, clock, cipher, flow, channel_id: str | None):
    return OAuthConnectionService(
        flow, FakeChannels(channel_id), cipher, uow_factory, redis, clock, prefix=redis_prefix
    )


async def connect(connections: OAuthConnectionService, tenant) -> object:
    user_id = uuid.uuid4()
    url = await connections.start(tenant, user_id)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return await connections.complete(state, "code-1", user_id=user_id)


async def test_connect_stores_an_encrypted_token_and_verifies_ownership(
    uow_factory, redis, redis_prefix, clock, cipher, tenant, session
) -> None:
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, FakeFlow(clock), CHANNEL_A)
    result = await connect(connections, tenant)
    assert result.status == "connected" and result.channel_id == CHANNEL_A
    token = (
        await session.execute(select(OAuthToken).where(OAuthToken.client_id == tenant.client_id))
    ).scalar_one()
    assert b"refresh-1" not in token.encrypted_payload and token.status == "active"
    assert token.google_subject == "google-sub-1"
    client = await session.get(Client, tenant.client_id)
    await session.refresh(client)
    assert client.own_channel_id == CHANNEL_A and client.own_channel_status == "verified"
    assert client.own_channel_verification[-1]["name"] == "oauth"
    active = await connections.access_token(tenant)
    assert active.channel_id == CHANNEL_A and active.access_token.get_secret_value() == "access-1"


async def test_a_different_channel_is_never_switched_silently(
    uow_factory, redis, redis_prefix, clock, cipher, session
) -> None:
    tenant = await create_tenant(session, "mismatch", own_channel_id=CHANNEL_B)
    flow = FakeFlow(clock)
    result = await connect(service(uow_factory, redis, redis_prefix, clock, cipher, flow, CHANNEL_A), tenant)
    assert result.status == "channel_mismatch" and result.expected_channel_id == CHANNEL_B
    assert flow.revoked == ["refresh-1"]
    assert (
        await session.scalar(
            select(func.count()).select_from(OAuthToken).where(OAuthToken.client_id == tenant.client_id)
        )
        == 0
    )


async def test_accounts_without_a_channel_and_expired_state(
    uow_factory, redis, redis_prefix, clock, cipher, tenant
) -> None:
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, FakeFlow(clock), None)
    assert (await connect(connections, tenant)).status == "no_channel"
    assert (
        await connections.complete("unknown-state", "code-1", user_id=uuid.uuid4())
    ).status == "invalid_state"


async def test_daily_pull_stores_owner_data_and_builds_the_panel(
    uow_factory, redis, redis_prefix, clock, cipher, tenant, session
) -> None:
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, FakeFlow(clock), CHANNEL_A)
    await connect(connections, tenant)
    reports = FakeReports()
    analytics = OwnAnalyticsService(connections, reports, uow_factory, YouTubeAnalyticsApiSettings(), clock)
    pulled = await analytics.daily_pull(tenant, CTX)
    assert pulled.connected and pulled.channel_days == 3 and pulled.videos == 1 and pulled.video_series == 1
    assert pulled.requests == len(reports.queries) == 7
    bundle = await analytics.load(tenant)
    assert bundle.connection.channel_id == CHANNEL_A
    assert len(bundle.channel_daily) == 3 and bundle.channel_daily[0].subscribers_gained == 5
    assert bundle.video_period[0].creator_content_type == "SHORTS"
    assert bundle.video_period[0].average_view_percentage == pytest.approx(71.5)
    assert {row.source_type for row in bundle.traffic_sources} == {"YT_SEARCH", "SHORTS"}
    assert bundle.search_terms == []
    assert bundle.video_daily_views[0].views == 40

    reports.queries.clear()
    clock.advance(timedelta(days=1))
    await analytics.daily_pull(tenant, CTX)
    daily_query = reports.queries[0]
    assert daily_query.start_date == bundle.data_through - timedelta(days=3)
    assert not any(
        query.filters == f"video=={video_id(1)}" and query.dimensions == ["day"] for query in reports.queries
    )


async def test_invalid_grant_purges_authorised_data(
    uow_factory, redis, redis_prefix, clock, cipher, tenant, session
) -> None:
    flow = FakeFlow(clock)
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, flow, CHANNEL_A)
    await connect(connections, tenant)
    analytics = OwnAnalyticsService(
        connections, FakeReports(), uow_factory, YouTubeAnalyticsApiSettings(), clock
    )
    await analytics.daily_pull(tenant, CTX)
    clock.advance(timedelta(hours=2))
    flow.refresh_error = InvalidGrant("Token has been expired or revoked.")
    pulled = await analytics.daily_pull(tenant, CTX)
    assert not pulled.connected
    for model in (OwnAnalyticsDaily, OwnTrafficSource, OwnSearchTerm, OwnVideoMetrics):
        assert (
            await session.scalar(
                select(func.count()).select_from(model).where(model.client_id == tenant.client_id)
            )
            == 0
        )
    status = await connections.status(tenant)
    assert status.status == "invalid_grant"
    assert await analytics.load(tenant) is None


async def test_disconnect_revokes_and_deletes_immediately(
    uow_factory, redis, redis_prefix, clock, cipher, tenant, session
) -> None:
    flow = FakeFlow(clock)
    connections = service(uow_factory, redis, redis_prefix, clock, cipher, flow, CHANNEL_A)
    await connect(connections, tenant)
    analytics = OwnAnalyticsService(
        connections, FakeReports(), uow_factory, YouTubeAnalyticsApiSettings(), clock
    )
    await analytics.daily_pull(tenant, CTX)
    assert await connections.disconnect(tenant)
    assert flow.revoked == ["refresh-1"]
    assert (
        await session.scalar(
            select(func.count())
            .select_from(OwnAnalyticsDaily)
            .where(OwnAnalyticsDaily.client_id == tenant.client_id)
        )
        == 0
    )
    token = (
        await session.execute(select(OAuthToken).where(OAuthToken.client_id == tenant.client_id))
    ).scalar_one()
    await session.refresh(token)
    assert token.status == "purged" and token.encrypted_payload == b""
    assert await connections.access_token(tenant) is None


@respx.mock
async def test_google_flow_uses_pkce_offline_access_and_maps_invalid_grant(clock) -> None:
    settings = GoogleOAuthSettings(client_id="client-1", client_secret=SecretStr("secret-1"))
    async with httpx.AsyncClient() as client:
        flow = GoogleOAuthFlow(client, settings, clock)
        query = parse_qs(urlsplit(flow.authorization_url("s1", "c1")).query)
        assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
        assert query["code_challenge_method"] == ["S256"] and "yt-analytics.readonly" in query["scope"][0]
        assert "monetary" not in query["scope"][0]
        respx.post(TOKEN_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "access_token": "a1",
                        "refresh_token": "r1",
                        "expires_in": 3599,
                        "scope": "openid https://www.googleapis.com/auth/yt-analytics.readonly",
                        "id_token": "x.eyJzdWIiOiAiMTIzIn0.y",
                    },
                ),
                httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "Token has been revoked."}
                ),
            ]
        )
        tokens = await flow.exchange("code", "verifier")
        assert tokens.subject == "123" and tokens.refresh_token.get_secret_value() == "r1"
        assert tokens.expires_at == NOW + timedelta(seconds=3599)
        with pytest.raises(InvalidGrant):
            await flow.refresh(SecretStr("r1"))
