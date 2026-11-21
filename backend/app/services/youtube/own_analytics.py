import json
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.adapters.youtube.errors import InvalidRequest, NoYouTubeChannel, Unauthorized
from app.adapters.youtube.oauth_google import InvalidGrant, TokenSet, pkce_pair
from app.core.clock import Clock
from app.core.config import YouTubeAnalyticsApiSettings
from app.core.db import UnitOfWork
from app.core.logging import get_logger
from app.core.security import SecretCipher
from app.core.tenancy import TenantScope
from app.domain.youtube import (
    OAuthConnectionStatus,
    OwnAnalytics,
    OwnDailyMetrics,
    OwnVideoDayViews,
    OwnVideoMetrics,
    QuotaPriority,
    SearchTermRow,
    TrafficSourceRow,
)
from app.models.own_analytics import OwnAnalyticsDaily, OwnSearchTerm, OwnTrafficSource
from app.models.own_analytics import OwnVideoMetrics as OwnVideoMetricsRow
from app.models.tenancy import Client, OAuthToken
from app.models.youtube_cache import YtChannel, YtVideo
from app.ports.youtube import AnalyticsQuery, AnalyticsTable, RequestContext
from app.repositories.shared.base import chunked
from app.repositories.tenant.oauth_tokens import OAuthTokenRepository
from app.services.youtube.quota import PacificDay

logger = get_logger(__name__)

OWN_TABLES = (OwnAnalyticsDaily, OwnTrafficSource, OwnSearchTerm, OwnVideoMetricsRow)
CHANNEL_DAILY_METRICS = [
    "views",
    "engagedViews",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "subscribersGained",
    "subscribersLost",
    "likes",
    "comments",
    "shares",
]
VIDEO_PERIOD_METRICS = [
    "views",
    "engagedViews",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "subscribersGained",
    "subscribersLost",
    "likes",
    "comments",
    "shares",
]
TRAFFIC_METRICS = ["views", "estimatedMinutesWatched"]
STATE_TTL_S = 600


class OAuthFlow(Protocol):
    def authorization_url(self, state: str, code_challenge: str) -> str: ...

    async def exchange(self, code: str, code_verifier: str) -> TokenSet: ...

    async def refresh(self, refresh_token: SecretStr) -> TokenSet: ...

    async def revoke(self, token: SecretStr) -> bool: ...


class OwnChannelLookup(Protocol):
    async def own_channel_id(self, access_token: SecretStr, ctx: RequestContext) -> str | None: ...


class AnalyticsReports(Protocol):
    async def report(
        self, access_token: SecretStr, query: AnalyticsQuery, ctx: RequestContext | None = None
    ) -> AnalyticsTable: ...


@dataclass(frozen=True)
class ActiveToken:
    token_id: uuid.UUID
    channel_id: str
    access_token: SecretStr


@dataclass(frozen=True)
class ConnectResult:
    status: Literal["connected", "channel_mismatch", "no_channel", "invalid_state"]
    channel_id: str | None = None
    expected_channel_id: str | None = None
    message: str | None = None


class AuthorizedDataPurger:
    """owner-authorised rows are deleted on revocation: in-app immediately, detected revocation within 24 h."""

    async def purge(self, uow: UnitOfWork, scope: TenantScope) -> int:
        deleted = 0
        for model in OWN_TABLES:
            result = await uow.session.execute(
                delete(model).where(
                    model.workspace_id == scope.workspace_id, model.client_id == scope.client_id
                )
            )
            deleted += result.rowcount
        return deleted


class OAuthConnectionService:
    def __init__(
        self,
        flow: OAuthFlow,
        channels: OwnChannelLookup,
        cipher: SecretCipher,
        uow_factory: Callable[[], UnitOfWork],
        redis: Redis,
        clock: Clock,
        *,
        purger: AuthorizedDataPurger | None = None,
        prefix: str = "gh",
    ) -> None:
        self._flow = flow
        self._channels = channels
        self._cipher = cipher
        self._uow_factory = uow_factory
        self._redis = redis
        self._clock = clock
        self._purger = purger or AuthorizedDataPurger()
        self._prefix = prefix

    def _state_key(self, state: str) -> str:
        return f"{self._prefix}:oauth_state:{state}"

    async def start(self, scope: TenantScope, user_id: uuid.UUID) -> str:
        if scope.client_id is None:
            raise ValueError("oauth connections are per client")
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        payload = {
            "workspace_id": str(scope.workspace_id),
            "client_id": str(scope.client_id),
            "user_id": str(user_id),
            "verifier": verifier,
        }
        await self._redis.set(self._state_key(state), json.dumps(payload), ex=STATE_TTL_S)
        return self._flow.authorization_url(state, challenge)

    async def complete(
        self, state: str, code: str, *, user_id: uuid.UUID, replace_own_channel: bool = False
    ) -> ConnectResult:
        """finishes a connection; only the user who started it may finish it, and the state is single use."""
        raw = await self._redis.getdel(self._state_key(state))
        if raw is None:
            return ConnectResult(
                status="invalid_state", message="This sign-in link expired. Please try again."
            )
        payload = json.loads(raw)
        if payload["user_id"] != str(user_id):
            return ConnectResult(
                status="invalid_state", message="Finish connecting YouTube in the account that started it."
            )
        scope = TenantScope(uuid.UUID(payload["workspace_id"]), uuid.UUID(payload["client_id"]))
        tokens = await self._flow.exchange(code, payload["verifier"])
        ctx = RequestContext(client_id=scope.client_id, run_id=None, priority=QuotaPriority.interactive)
        try:
            channel_id = await self._channels.own_channel_id(tokens.access_token, ctx)
        except NoYouTubeChannel:
            channel_id = None
        if channel_id is None:
            await self._flow.revoke(tokens.refresh_token or tokens.access_token)
            return ConnectResult(status="no_channel", message="This Google account has no YouTube channel.")
        async with self._uow_factory() as uow:
            client = await uow.session.get(Client, scope.client_id)
            expected = client.own_channel_id if client else None
            if expected and expected != channel_id and not replace_own_channel:
                await self._flow.revoke(tokens.refresh_token or tokens.access_token)
                return ConnectResult(
                    status="channel_mismatch",
                    channel_id=channel_id,
                    expected_channel_id=expected,
                    message="You connected a different channel than the one this workspace tracks. Switch?",
                )
            await self._store_token(uow, scope, uuid.UUID(payload["user_id"]), channel_id, tokens)
            if client is not None:
                signal = {
                    "name": "oauth",
                    "weight": 1.0,
                    "detail": "owner signed in with this channel",
                    "at": self._clock.now().isoformat(),
                }
                client.own_channel_id, client.own_channel_status = channel_id, "verified"
                client.own_channel_verification = [*(client.own_channel_verification or []), signal]
            await uow.commit()
        return ConnectResult(status="connected", channel_id=channel_id)

    async def _store_token(
        self, uow: UnitOfWork, scope: TenantScope, user_id: uuid.UUID, channel_id: str, tokens: TokenSet
    ) -> None:
        if tokens.refresh_token is None:
            raise InvalidGrant("google returned no refresh token; consent must be granted again")
        now = self._clock.now()
        await uow.session.execute(
            update(OAuthToken)
            .where(OAuthToken.client_id == scope.client_id, OAuthToken.status == "active")
            .values(status="revoked", revoked_at=now, revocation_source="in_app")
        )
        token_id = uuid.uuid4()
        key_id, ciphertext = self._cipher.encrypt(self._payload(tokens), binding=str(token_id))
        uow.session.add(
            OAuthToken(
                id=token_id,
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                google_subject=tokens.subject or f"channel:{channel_id}",
                channel_id=channel_id,
                scopes=tokens.scopes,
                key_id=key_id,
                encrypted_payload=ciphertext,
                approved_viewer_user_ids=[],
                granted_by_user_id=user_id,
                granted_at=now,
                last_refreshed_at=now,
                authorization_verified_at=now,
            )
        )
        await uow.session.flush()

    @staticmethod
    def _payload(tokens: TokenSet) -> bytes:
        return json.dumps(
            {
                "access_token": tokens.access_token.get_secret_value(),
                "refresh_token": tokens.refresh_token.get_secret_value() if tokens.refresh_token else None,
                "expires_at": tokens.expires_at.isoformat(),
            }
        ).encode()

    async def _active(self, uow: UnitOfWork, scope: TenantScope) -> OAuthToken | None:
        return await OAuthTokenRepository(uow.session, scope).active_for_client()

    def _decrypt(self, token: OAuthToken) -> dict[str, Any]:
        return json.loads(self._cipher.decrypt(token.encrypted_payload, binding=str(token.id)))

    async def access_token(self, scope: TenantScope) -> "ActiveToken | None":
        """a valid access token for the client's connection, refreshed when expired; none when not connected."""
        async with self._uow_factory() as uow:
            token = await self._active(uow, scope)
            if token is None:
                return None
            payload = self._decrypt(token)
            expires_at = datetime.fromisoformat(payload["expires_at"])
            if expires_at - self._clock.now() > timedelta(minutes=2):
                return ActiveToken(token.id, token.channel_id, SecretStr(payload["access_token"]))
            try:
                fresh = await self._flow.refresh(SecretStr(payload["refresh_token"]))
            except InvalidGrant as error:
                await self._invalidate(uow, scope, token, str(error))
                await uow.commit()
                return None
            key_id, ciphertext = self._cipher.encrypt(self._payload(fresh), binding=str(token.id))
            token.key_id, token.encrypted_payload = key_id, ciphertext
            token.last_refreshed_at, token.failure_count = self._clock.now(), 0
            await uow.commit()
            return ActiveToken(token.id, token.channel_id, fresh.access_token)

    async def _invalidate(self, uow: UnitOfWork, scope: TenantScope, token: OAuthToken, reason: str) -> None:
        now = self._clock.now()
        token.status, token.revoked_at, token.revocation_source = "invalid", now, "invalid_grant"
        token.last_error, token.purge_due_at = reason[:200], now + timedelta(hours=24)
        await self._purger.purge(uow, scope)
        await self._mark_client(uow, scope)
        logger.info("oauth_invalid_grant", client_id=str(scope.client_id))

    async def _mark_client(self, uow: UnitOfWork, scope: TenantScope) -> None:
        await uow.session.execute(
            update(Client)
            .where(Client.id == scope.client_id, Client.own_channel_status == "verified")
            .values(own_channel_status="needs_attention")
        )

    async def disconnect(self, scope: TenantScope) -> bool:
        async with self._uow_factory() as uow:
            token = await self._active(uow, scope)
            if token is None:
                return False
            payload = self._decrypt(token)
            await self._flow.revoke(SecretStr(payload.get("refresh_token") or payload["access_token"]))
            now = self._clock.now()
            await self._purger.purge(uow, scope)
            token.status, token.revoked_at, token.revocation_source = "purged", now, "in_app"
            token.encrypted_payload, token.purge_due_at = b"", now
            await self._mark_client(uow, scope)
            await uow.commit()
        return True

    async def status(self, scope: TenantScope) -> OAuthConnectionStatus | None:
        statuses = {"active": "active", "invalid": "invalid_grant", "revoked": "revoked", "purged": "revoked"}
        async with self._uow_factory() as uow:
            statement = (
                select(OAuthToken)
                .where(OAuthToken.workspace_id == scope.workspace_id, OAuthToken.client_id == scope.client_id)
                .order_by(OAuthToken.granted_at.desc())
                .limit(1)
            )
            token = (await uow.session.execute(statement)).scalar_one_or_none()
            if token is None:
                return None
            return OAuthConnectionStatus(
                status=statuses[token.status],
                channel_id=token.channel_id,
                scopes=list(token.scopes),
                last_verified_at=token.authorization_verified_at,
            )


def _rows(table: AnalyticsTable) -> list[dict[str, Any]]:
    return [dict(zip(table.column_headers, row, strict=False)) for row in table.rows]


def _int(value: Any) -> int:
    return int(value or 0)


@dataclass
class PullReport:
    requests: int = 0
    data_through: date | None = None
    channel_days: int = 0
    videos: int = 0
    video_series: int = 0
    traffic_rows: int = 0
    search_terms: int = 0
    connected: bool = True


class OwnAnalyticsService:
    """daily pull of the owner's channel analytics; kept tenant-scoped and never merged with public data."""

    def __init__(
        self,
        connections: OAuthConnectionService,
        reports: AnalyticsReports,
        uow_factory: Callable[[], UnitOfWork],
        settings: YouTubeAnalyticsApiSettings,
        clock: Clock,
        *,
        timezone: str = "America/Los_Angeles",
        video_series_per_pull: int = 50,
        video_series_horizon_days: int = 31,
    ) -> None:
        self._connections = connections
        self._reports = reports
        self._uow_factory = uow_factory
        self._settings = settings
        self._clock = clock
        self._pacific = PacificDay(timezone)
        self._series_per_pull = video_series_per_pull
        self._series_horizon_days = video_series_horizon_days

    async def daily_pull(self, scope: TenantScope, ctx: RequestContext) -> PullReport:
        report = PullReport()
        connection = await self._connections.access_token(scope)
        if connection is None:
            report.connected = False
            return report
        token_id, access = connection.token_id, connection.access_token
        today = self._pacific.day(self._clock.now())
        period_start = today - timedelta(days=self._settings.backfill_days)
        start = await self._channel_daily_start(
            scope, connection.channel_id, today - timedelta(days=self._settings.channel_daily_days)
        )

        async def run(query: AnalyticsQuery) -> list[dict[str, Any]]:
            report.requests += 1
            return _rows(await self._reports.report(access, query, ctx))

        base = {"channel_id": connection.channel_id, "end_date": today}
        try:
            daily = await run(
                AnalyticsQuery(
                    **base, start_date=start, metrics=CHANNEL_DAILY_METRICS, dimensions=["day"], sort="day"
                )
            )
            videos = await self._top_videos(run, base, period_start)
            traffic = await run(
                AnalyticsQuery(
                    **base,
                    start_date=period_start,
                    metrics=TRAFFIC_METRICS,
                    dimensions=["insightTrafficSourceType"],
                )
            )
            terms = await run(self._terms_query(base, period_start, None))
            detail_ids = [row["video"] for row in videos[: self._settings.per_video_detail]]
            per_video_traffic, per_video_terms = {}, {}
            for video_id in detail_ids:
                per_video_traffic[video_id] = await run(
                    AnalyticsQuery(
                        **base,
                        start_date=period_start,
                        metrics=TRAFFIC_METRICS,
                        dimensions=["insightTrafficSourceType"],
                        filters=f"video=={video_id}",
                    )
                )
                per_video_terms[video_id] = await run(self._terms_query(base, period_start, video_id))
            recent = await self._recent_uploads(connection.channel_id, today)
            series = await self._video_series(
                scope, run, base, [*(row["video"] for row in videos), *recent], period_start
            )
        except Unauthorized:
            logger.info("own_analytics_unauthorized", client_id=str(scope.client_id))
            report.connected = False
            return report
        report.data_through = max((date.fromisoformat(row["day"]) for row in daily), default=None)
        async with self._uow_factory() as uow:
            await self._save(
                uow,
                scope,
                token_id,
                daily,
                videos,
                traffic,
                terms,
                per_video_traffic,
                per_video_terms,
                series,
                period_start,
                today,
            )
            await uow.commit()
        report.channel_days, report.videos, report.video_series = len(daily), len(videos), len(series)
        report.traffic_rows = len(traffic) + sum(len(rows) for rows in per_video_traffic.values())
        report.search_terms = len(terms) + sum(len(rows) for rows in per_video_terms.values())
        return report

    def _terms_query(self, base: dict, start: date, video_id: str | None) -> AnalyticsQuery:
        filters = "insightTrafficSourceType==YT_SEARCH" + (f";video=={video_id}" if video_id else "")
        return AnalyticsQuery(
            **base,
            start_date=start,
            metrics=TRAFFIC_METRICS,
            dimensions=["insightTrafficSourceDetail"],
            filters=filters,
            sort="-views",
            max_results=self._settings.search_terms,
        )

    async def _top_videos(self, run, base: dict, start: date) -> list[dict[str, Any]]:
        query = dict(
            base,
            start_date=start,
            metrics=VIDEO_PERIOD_METRICS,
            sort="-views",
            max_results=self._settings.top_videos,
        )
        try:
            return await run(AnalyticsQuery(**query, dimensions=["video", "creatorContentType"]))
        except InvalidRequest:
            # the creatorContentType pairing is unverified; fall back to the documented top-videos report
            return await run(AnalyticsQuery(**query, dimensions=["video"]))

    async def _channel_daily_start(self, scope: TenantScope, channel_id: str, period_start: date) -> date:
        # analytics lag 48-72 h, so the last few pacific days are always pulled again
        async with self._uow_factory() as uow:
            earliest, latest = (
                await uow.session.execute(
                    select(func.min(OwnAnalyticsDaily.day), func.max(OwnAnalyticsDaily.day)).where(
                        OwnAnalyticsDaily.client_id == scope.client_id, OwnAnalyticsDaily.video_id == ""
                    )
                )
            ).one()
            created = await uow.session.scalar(
                select(YtChannel.published_at).where(YtChannel.channel_id == channel_id)
            )
        if latest is None:
            return period_start
        # history pulled under a shorter window is backfilled once; a younger channel simply has less
        if created is not None and earliest > max(period_start, created.date()):
            return period_start
        return max(period_start, latest - timedelta(days=self._settings.repull_days))

    async def _recent_uploads(self, channel_id: str, today: date) -> list[str]:
        # uploads still inside the views-at-age horizon need a series even when they miss the top list
        since = datetime.combine(today - timedelta(days=self._series_horizon_days), datetime.min.time(), UTC)
        async with self._uow_factory() as uow:
            return list(
                (
                    await uow.session.execute(
                        select(YtVideo.video_id)
                        .where(YtVideo.channel_id == channel_id, YtVideo.effective_published_at >= since)
                        .order_by(YtVideo.effective_published_at.desc())
                    )
                ).scalars()
            )

    async def _video_series(
        self, scope: TenantScope, run, base: dict, video_ids: Sequence[str], period_start: date
    ) -> dict[str, list[dict[str, Any]]]:
        wanted = list(dict.fromkeys(video_ids))
        async with self._uow_factory() as uow:
            spans = {
                video_id: (first, last)
                for video_id, first, last in (
                    await uow.session.execute(
                        select(
                            OwnAnalyticsDaily.video_id,
                            func.min(OwnAnalyticsDaily.day),
                            func.max(OwnAnalyticsDaily.day),
                        )
                        .where(
                            OwnAnalyticsDaily.client_id == scope.client_id,
                            OwnAnalyticsDaily.video_id.in_(wanted),
                        )
                        .group_by(OwnAnalyticsDaily.video_id)
                    )
                ).all()
            }
        horizon = timedelta(days=self._series_horizon_days)
        # never-pulled first, then series that stop short of the horizon, stalest first
        unpulled = [video_id for video_id in wanted if video_id not in spans]
        incomplete = sorted(
            (video_id for video_id, (first, last) in spans.items() if last - first < horizon),
            key=lambda video_id: spans[video_id][1],
        )
        series = {}
        for video_id in [*unpulled, *incomplete][: self._series_per_pull]:
            series[video_id] = await run(
                AnalyticsQuery(
                    **base,
                    start_date=period_start,
                    metrics=["views"],
                    dimensions=["day"],
                    filters=f"video=={video_id}",
                    sort="day",
                )
            )
        return series

    def _series_rows(self, rows: list[dict[str, Any]], through: date | None) -> list[tuple[date, int]]:
        # a pulled range is complete: days the api left out up to data_through had no views
        views = {date.fromisoformat(row["day"]): _int(row.get("views")) for row in rows}
        if not views or through is None:
            return sorted(views.items())
        first = min(views)
        last = min(through, first + timedelta(days=self._series_horizon_days))
        filled = {first + timedelta(days=offset): 0 for offset in range((last - first).days + 1)}
        return sorted({**filled, **views}.items())

    async def _save(
        self,
        uow: UnitOfWork,
        scope: TenantScope,
        token_id: uuid.UUID,
        daily,
        videos,
        traffic,
        terms,
        per_video_traffic,
        per_video_terms,
        series,
        period_start: date,
        today: date,
    ) -> None:
        now = self._clock.now()
        common = {
            "workspace_id": scope.workspace_id,
            "client_id": scope.client_id,
            "oauth_token_id": token_id,
            "fetched_at": now,
        }
        daily_rows = [
            {
                **common,
                "video_id": "",
                "day": date.fromisoformat(row["day"]),
                "views": _int(row.get("views")),
                "engaged_views": row.get("engagedViews"),
                "estimated_minutes_watched": row.get("estimatedMinutesWatched"),
                "average_view_duration_s": row.get("averageViewDuration"),
                "subscribers_gained": row.get("subscribersGained"),
                "subscribers_lost": row.get("subscribersLost"),
                "likes": row.get("likes"),
                "comments": row.get("comments"),
                "shares": row.get("shares"),
            }
            for row in daily
        ]
        through = max((row["day"] for row in daily_rows), default=None)
        daily_rows += [
            {**common, "video_id": video_id, "day": day, "views": views}
            for video_id, rows in series.items()
            for day, views in self._series_rows(rows, through)
        ]
        await _upsert(uow, OwnAnalyticsDaily, daily_rows, ["client_id", "video_id", "day"])
        content_types = {row["video"]: row.get("creatorContentType") for row in videos}
        await _upsert(
            uow,
            OwnVideoMetricsRow,
            [
                {
                    **common,
                    "video_id": row["video"],
                    "period_start": period_start,
                    "period_end": today,
                    "creator_content_type": content_types.get(row["video"]),
                    "views": _int(row.get("views")),
                    "engaged_views": row.get("engagedViews"),
                    "estimated_minutes_watched": row.get("estimatedMinutesWatched"),
                    "average_view_duration_s": row.get("averageViewDuration"),
                    "average_view_percentage": row.get("averageViewPercentage"),
                    "subscribers_gained": row.get("subscribersGained"),
                    "subscribers_lost": row.get("subscribersLost"),
                    "likes": row.get("likes"),
                    "comments": row.get("comments"),
                    "shares": row.get("shares"),
                }
                for row in videos
            ],
            ["client_id", "video_id", "period_start", "period_end"],
        )
        period = {"period_start": period_start, "period_end": today}
        traffic_rows = [
            {
                **common,
                **period,
                "video_id": video_id,
                "source_type": row["insightTrafficSourceType"],
                "views": _int(row.get("views")),
                "estimated_minutes_watched": row.get("estimatedMinutesWatched"),
            }
            for video_id, rows in [("", traffic), *per_video_traffic.items()]
            for row in rows
        ]
        await _upsert(
            uow,
            OwnTrafficSource,
            traffic_rows,
            ["client_id", "video_id", "period_start", "period_end", "source_type"],
        )
        term_rows = [
            {
                **common,
                **period,
                "video_id": video_id,
                "term": row["insightTrafficSourceDetail"],
                "views": _int(row.get("views")),
                "estimated_minutes_watched": row.get("estimatedMinutesWatched"),
            }
            for video_id, rows in [("", terms), *per_video_terms.items()]
            for row in rows
        ]
        await _upsert(
            uow, OwnSearchTerm, term_rows, ["client_id", "video_id", "period_start", "period_end", "term"]
        )

    async def load(self, scope: TenantScope) -> OwnAnalytics | None:
        """the owner-authorised panel bundle, labelled 'YouTube Analytics (owner-authorised)' by the ui."""
        connection = await self._connections.status(scope)
        if connection is None or connection.status != "active":
            return None
        async with self._uow_factory() as uow:
            session = uow.session

            async def rows(model) -> list:
                statement = select(model).where(
                    model.workspace_id == scope.workspace_id, model.client_id == scope.client_id
                )
                return list((await session.execute(statement)).scalars())

            daily, metrics, traffic, terms = (
                await rows(OwnAnalyticsDaily),
                await rows(OwnVideoMetricsRow),
                await rows(OwnTrafficSource),
                await rows(OwnSearchTerm),
            )
            session.expunge_all()
        channel_daily = sorted((row for row in daily if row.video_id == ""), key=lambda row: row.day)
        return OwnAnalytics(
            connection=connection,
            data_through=channel_daily[-1].day if channel_daily else self._pacific.day(self._clock.now()),
            channel_daily=[
                OwnDailyMetrics(
                    day=row.day,
                    views=row.views,
                    engaged_views=row.engaged_views,
                    estimated_minutes_watched=row.estimated_minutes_watched or 0,
                    average_view_duration_s=float(row.average_view_duration_s or 0),
                    subscribers_gained=row.subscribers_gained or 0,
                    subscribers_lost=row.subscribers_lost or 0,
                    likes=row.likes or 0,
                    comments=row.comments or 0,
                    shares=row.shares or 0,
                )
                for row in channel_daily
            ],
            video_period=[
                OwnVideoMetrics(
                    video_id=row.video_id,
                    period_start=row.period_start,
                    period_end=row.period_end,
                    creator_content_type=row.creator_content_type,
                    views=row.views,
                    engaged_views=row.engaged_views,
                    estimated_minutes_watched=row.estimated_minutes_watched or 0,
                    average_view_duration_s=row.average_view_duration_s or 0.0,
                    average_view_percentage=row.average_view_percentage or 0.0,
                    subscribers_gained=row.subscribers_gained or 0,
                    subscribers_lost=row.subscribers_lost or 0,
                    likes=row.likes or 0,
                    comments=row.comments or 0,
                    shares=row.shares or 0,
                )
                for row in metrics
            ],
            video_daily_views=[
                OwnVideoDayViews(video_id=row.video_id, day=row.day, views=row.views)
                for row in daily
                if row.video_id
            ],
            traffic_sources=[
                TrafficSourceRow(
                    video_id=row.video_id or None,
                    period_start=row.period_start,
                    period_end=row.period_end,
                    source_type=row.source_type,
                    views=row.views,
                    estimated_minutes_watched=row.estimated_minutes_watched or 0,
                )
                for row in traffic
            ],
            search_terms=[
                SearchTermRow(
                    video_id=row.video_id or None,
                    period_start=row.period_start,
                    period_end=row.period_end,
                    term=row.term,
                    views=row.views,
                    estimated_minutes_watched=row.estimated_minutes_watched or 0,
                )
                for row in terms
            ],
        )

    async def verify(self, scope: TenantScope, ctx: RequestContext) -> bool:
        """monthly check that the authorisation still works; stored analytics are purged when it does not."""
        connection = await self._connections.access_token(scope)
        if connection is None:
            return False
        today = self._pacific.day(self._clock.now())
        query = AnalyticsQuery(
            channel_id=connection.channel_id, start_date=today, end_date=today, metrics=["views"]
        )
        await self._reports.report(connection.access_token, query, ctx)
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(OAuthToken)
                .where(OAuthToken.id == connection.token_id)
                .values(authorization_verified_at=self._clock.now())
            )
            await uow.commit()
        return True


async def _upsert(uow: UnitOfWork, model, rows: list[dict[str, Any]], keys: list[str]) -> None:
    if not rows:
        return
    unique = list({tuple(row[key] for key in keys): row for row in rows}.values())
    for batch in chunked(unique, len(model.__table__.columns)):
        columns = set(batch[0])
        statement = insert(model).values(list(batch))
        statement = statement.on_conflict_do_update(
            index_elements=keys, set_={name: statement.excluded[name] for name in columns if name not in keys}
        )
        await uow.session.execute(statement)
