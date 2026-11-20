from functools import cached_property
from typing import Any

from app.adapters.youtube.analytics_v2 import AnalyticsApiGateway
from app.adapters.youtube.cached import CachedGateway
from app.adapters.youtube.data_api_v3 import DataApiV3Gateway
from app.adapters.youtube.http import HttpHandler, RequestHandler
from app.adapters.youtube.middleware import build_handler_chain
from app.adapters.youtube.oauth_google import GoogleOAuthFlow
from app.core.registry import adapter_registry
from app.services.youtube.cache_policy import FreshnessPolicy
from app.services.youtube.comments import CommentSampler
from app.services.youtube.format_classifier import FormatClassifier
from app.services.youtube.ingest import IngestService
from app.services.youtube.ledger import CacheHitCounter, QuotaLedger
from app.services.youtube.own_analytics import OAuthConnectionService, OwnAnalyticsService
from app.services.youtube.ownership import OwnershipVerifier
from app.services.youtube.quota import QuotaManager
from app.services.youtube.refresh import YouTubeCacheRefresher
from app.services.youtube.resolve import ChannelResolver
from app.services.youtube.search import CachedSearchService
from app.services.youtube.single_flight import InflightClaims
from app.services.youtube.snapshots import (
    CadencePlanner,
    SnapshotCollector,
    SnapshotCompactor,
    SnapshotReader,
    SnapshotScheduler,
    counting_regime_for,
)
from app.services.youtube.store import YouTubeStore


class YouTubeDataLayer:
    """composition of the youtube data layer from the container; everything is built lazily."""

    def __init__(self, container: Any) -> None:
        self._container = container
        self.settings = container.settings
        self.youtube = container.settings.youtube
        self.clock = container.clock

    @cached_property
    def store(self) -> YouTubeStore:
        return YouTubeStore(self._container.uow)

    @cached_property
    def quota(self) -> QuotaManager:
        container = self._container
        return QuotaManager(
            container.quota_counter,
            container.redis,
            self.settings.quota,
            self.clock,
            prefix=self.settings.redis.key_prefix,
        )

    @cached_property
    def ledger(self) -> QuotaLedger:
        return QuotaLedger(
            self._container.uow,
            self.clock,
            project=self.settings.quota.project,
            timezone=self.settings.quota.timezone,
        )

    @cached_property
    def cache_hits(self) -> CacheHitCounter:
        return CacheHitCounter(
            self._container.redis,
            self.clock,
            prefix=self.settings.redis.key_prefix,
            timezone=self.settings.quota.timezone,
        )

    @cached_property
    def handler(self) -> RequestHandler:
        http = HttpHandler(
            self._container.http, self.youtube.api_key, timeout_s=self.youtube.request_timeout_s
        )
        return build_handler_chain(
            http,
            quota=self.quota,
            ledger=self.ledger,
            clock=self.clock,
            settings=self.youtube.retry,
            rate_limiter=self._container.rate_limiter,
        )

    @cached_property
    def classifier(self) -> FormatClassifier:
        return FormatClassifier(self.youtube.formats)

    @cached_property
    def counting_regime(self):
        return counting_regime_for(self.youtube.snapshots)

    @cached_property
    def data_api(self) -> DataApiV3Gateway:
        return DataApiV3Gateway(
            self.handler,
            self.classifier,
            self.clock,
            base_url=self.youtube.base_url,
            freshness=self.youtube.freshness,
            counting_regime=self.counting_regime,
            embed_max_width=self.youtube.formats.embed_max_width,
        )

    @cached_property
    def gateway(self) -> CachedGateway:
        container = self._container
        return CachedGateway(
            self.data_api,
            self.store,
            FreshnessPolicy(self.youtube.freshness),
            self.clock,
            locks=container.lock_manager,
            inflight=InflightClaims(
                container.redis, prefix=self.settings.redis.key_prefix, ttl_s=self.youtube.inflight_ttl_s
            ),
            hits=self.cache_hits,
            coalesce_window_s=self.youtube.coalesce_window_ms / 1000,
            counting_regime=self.counting_regime,
        )

    @cached_property
    def analytics_gateway(self) -> AnalyticsApiGateway:
        return AnalyticsApiGateway(self.handler, base_url=self.youtube.analytics_api.base_url)

    @cached_property
    def verifier(self) -> OwnershipVerifier:
        return OwnershipVerifier(self.youtube.ownership, self.store)

    @cached_property
    def resolver(self) -> ChannelResolver:
        return ChannelResolver(self.gateway, self.verifier)

    @cached_property
    def scheduler(self) -> SnapshotScheduler:
        horizon = self.youtube.ingest.window_max_days + self.youtube.ingest.baseline_extra_days
        return SnapshotScheduler(self.store, CadencePlanner(self.youtube.snapshots, horizon), self.clock)

    @cached_property
    def collector(self) -> SnapshotCollector:
        return SnapshotCollector(self.gateway, self.scheduler, self.youtube.snapshots, self.clock)

    @cached_property
    def compactor(self) -> SnapshotCompactor:
        return SnapshotCompactor(self.store, self.youtube.snapshots, self.clock)

    @cached_property
    def reader(self) -> SnapshotReader:
        return SnapshotReader(self.store)

    @cached_property
    def ingest(self) -> IngestService:
        return IngestService(
            self.gateway, self.store, self.scheduler, self.youtube.ingest, self.youtube.freshness, self.clock
        )

    @cached_property
    def comments(self) -> CommentSampler:
        return CommentSampler(self.gateway, self.store, self.youtube.comments, self.clock)

    @cached_property
    def search(self) -> CachedSearchService:
        return CachedSearchService(self.gateway)

    @cached_property
    def oauth(self) -> OAuthConnectionService:
        container = self._container
        flow = GoogleOAuthFlow(container.http, self.settings.google_oauth, self.clock)
        return OAuthConnectionService(
            flow,
            self.gateway,
            container.cipher,
            container.uow,
            container.redis,
            self.clock,
            prefix=self.settings.redis.key_prefix,
        )

    @cached_property
    def own_analytics(self) -> OwnAnalyticsService:
        return OwnAnalyticsService(
            self.oauth,
            self.analytics_gateway,
            self._container.uow,
            self.youtube.analytics_api,
            self.clock,
            timezone=self.settings.quota.timezone,
        )

    async def aclose(self) -> None:
        if "ledger" in self.__dict__:
            await self.ledger.flush()


@adapter_registry.register("youtube_data_layer", "default")
def build_data_layer(container: Any) -> YouTubeDataLayer:
    return YouTubeDataLayer(container)


@adapter_registry.register("youtube", "youtube_data_api")
def build_youtube_gateway(container: Any) -> CachedGateway:
    return container.adapter("youtube_data_layer").gateway


@adapter_registry.register("youtube_analytics", "youtube_analytics_v2")
def build_analytics_gateway(container: Any) -> AnalyticsApiGateway:
    return container.adapter("youtube_data_layer").analytics_gateway


@adapter_registry.register("retention_refresher", "youtube_refresh")
def build_cache_refresher(container: Any) -> YouTubeCacheRefresher:
    return YouTubeCacheRefresher(container.adapter("youtube_data_layer").gateway)
