import hashlib
import json
from functools import cached_property
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from app.adapters.llm.factory import build_llm_client
from app.api.errors import NotFound, Unavailable
from app.api.services_impl.settings import LLMConfigService
from app.core.config import LLMSettings
from app.core.logging import get_logger
from app.core.registry import UnknownRegistrationError
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind
from app.pipeline.inputs.cards import TemplateCardInputs
from app.pipeline.inputs.news import SqlChannelBaselines, TableNewsContextLoader
from app.pipeline.inputs.recommend import RunRecommendInputs
from app.pipeline.inputs.refresh import RunRefreshInputs
from app.services.analytics.storage import AnalyticsStore
from app.services.llm.budgets import RedisBudgetLedger
from app.services.llm.writer.copy_store import RecommendationCopyStore
from app.services.llm.writer.service import CardWritingService, build_card_writing_service
from app.services.news.factory import build_news_radar
from app.services.news.service import NewsRadarService
from app.services.recommend.feedback import OutcomeTracker
from app.services.recommend.llm_writer import CardWritingServiceWriter
from app.services.recommend.repository import SqlRecommendationStore
from app.services.refresh.links.checker import LinkChecker
from app.services.refresh.links.health import LinkStatusCache
from app.services.refresh.store import SqlLinkStatusCache, SqlRefreshStore

logger = get_logger(__name__)


def llm_settings_key(settings: LLMSettings) -> str:
    # model_dump_json masks secrets, so the keys are hashed in explicitly to notice a rotated workspace key
    dumped = settings.model_dump(mode="python")

    def reveal(value: Any) -> Any:
        if isinstance(value, SecretStr):
            return value.get_secret_value()
        if isinstance(value, dict):
            return {key: reveal(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [reveal(item) for item in value]
        return value

    material = json.dumps(reveal(dumped), sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class SettingsOverlay:
    """the container with one workspace's llm settings swapped in, for building that workspace's client."""

    def __init__(self, container: Any, llm: LLMSettings) -> None:
        self._container = container
        self.settings = container.settings.model_copy(update={"llm": llm})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._container, name)


class PipelineDeps:
    """what stages read from ctx.deps: named collaborators here, everything else falls through to the container."""

    def __init__(self, container: Any) -> None:
        self.container = container
        self._workspace_llms: dict[tuple[UUID, str], Any] = {}

    def __getattr__(self, name: str) -> Any:
        # only reached for names not defined here (settings, session_factory, uow, adapter, clock, ...)
        return getattr(self.container, name)

    def optional_adapter(self, port: str) -> Any | None:
        try:
            return self.container.adapter(port)
        except (LookupError, UnknownRegistrationError):
            return None

    @cached_property
    def llm(self) -> Any | None:
        return self.optional_adapter("llm") if self.container.settings.llm.enabled else None

    @cached_property
    def analytics_inputs(self) -> Any:
        return self.container.adapter("analytics_inputs")

    @cached_property
    def analytics_store(self) -> AnalyticsStore:
        return AnalyticsStore(self.container.session_factory)

    @cached_property
    def image_source(self) -> Any:
        return self.container.adapter("image_source")

    @cached_property
    def thumbnail_text_reader(self) -> Any:
        return self.container.adapter("thumbnail_text_reader")

    @cached_property
    def recommend_settings(self) -> Any:
        return self.container.settings.recommend

    @cached_property
    def recommend_inputs(self) -> RunRecommendInputs:
        return RunRecommendInputs(self.container, self.analytics_inputs, self.recommend_settings)

    @cached_property
    def card_writing(self) -> CardWritingService | None:
        if self.llm is None:
            return None
        return build_card_writing_service(self.llm, self.container.settings.llm.writer)

    @cached_property
    def card_writer(self) -> CardWritingServiceWriter | None:
        # one writer for every card (99 C102): recommend words the cards through 10's writing service
        return CardWritingServiceWriter(self.card_writing) if self.card_writing is not None else None

    @cached_property
    def recommendation_store(self) -> SqlRecommendationStore:
        tracker = OutcomeTracker(auto_done_days=self.recommend_settings.feedback.auto_done_days)
        return SqlRecommendationStore(self.container.uow, tracker)

    @cached_property
    def card_inputs(self) -> TemplateCardInputs:
        return TemplateCardInputs(self.container)

    @cached_property
    def llm_budget(self) -> RedisBudgetLedger:
        settings = self.container.settings
        return RedisBudgetLedger(self.container.redis, settings.redis.key_prefix, settings.llm.budgets)

    @cached_property
    def recommendation_copy(self) -> RecommendationCopyStore:
        return RecommendationCopyStore(self.container.session_factory)

    @cached_property
    def refresh_settings(self) -> Any:
        return self.container.settings.refresh

    @cached_property
    def refresh_inputs(self) -> RunRefreshInputs:
        return RunRefreshInputs(self.container, self.analytics_inputs)

    @cached_property
    def link_probe(self) -> LinkChecker:
        return LinkChecker(self.container.http, self.refresh_settings, self.container.clock)

    @cached_property
    def link_cache(self) -> LinkStatusCache:
        return SqlLinkStatusCache(self.container.session_factory)

    @cached_property
    def news_context(self) -> TableNewsContextLoader:
        return TableNewsContextLoader(self.container)

    @cached_property
    def news_radar(self) -> NewsRadarService:
        youtube = self.optional_adapter("youtube_data_layer")
        return build_news_radar(
            self.container,
            llm=self.llm,
            gateway=youtube.gateway if youtube is not None else None,
            baselines=SqlChannelBaselines(self.container.session_factory),
            embedder=self.optional_adapter("embedder"),
        )

    async def workspace_llm(self, workspace_id: UUID) -> Any | None:
        """the llm client for one workspace: deployment settings plus that workspace's PUT /settings/llm overrides."""
        try:
            effective = await LLMConfigService(self.container).effective(workspace_id)
        except (NotFound, Unavailable) as error:
            logger.warning(
                "llm.workspace_settings_unusable", workspace_id=str(workspace_id), reason=str(error)[:200]
            )
            return self.llm
        if not effective.enabled:
            return None
        key = llm_settings_key(effective)
        if key == llm_settings_key(self.container.settings.llm):
            return self.llm
        cached = self._workspace_llms.get((workspace_id, key))
        if cached is None:
            cached = build_llm_client(SettingsOverlay(self.container, effective))
            self._workspace_llms[(workspace_id, key)] = cached
        return cached

    async def for_run(self, scope: TenantScope, kind: RunKind) -> "RunDeps":
        return RunDeps(self, scope, kind, await self.workspace_llm(scope.workspace_id))


class RunDeps:
    """per-run view: tenant-scoped stores for one run, the shared collaborators otherwise."""

    def __init__(self, shared: PipelineDeps, scope: TenantScope, kind: RunKind, llm: Any | None) -> None:
        self.shared = shared
        self.scope = scope
        self.kind = kind
        self.llm = llm

    def __getattr__(self, name: str) -> Any:
        return getattr(self.shared, name)

    def adapter(self, port: str) -> Any:
        # components that look the llm up by port get this workspace's client too
        if port == "llm":
            if self.llm is None:
                raise LookupError("llm is disabled for this workspace")
            return self.llm
        return self.shared.container.adapter(port)

    @cached_property
    def card_writing(self) -> CardWritingService | None:
        if self.llm is self.shared.llm:
            return self.shared.card_writing
        return (
            build_card_writing_service(self.llm, self.settings.llm.writer) if self.llm is not None else None
        )

    @cached_property
    def card_writer(self) -> CardWritingServiceWriter | None:
        return CardWritingServiceWriter(self.card_writing) if self.card_writing is not None else None

    @cached_property
    def recommendation_store(self) -> SqlRecommendationStore | None:
        # a backtest replays history; persisting its cards would expire the client's live ones
        return None if self.kind is RunKind.backtest else self.shared.recommendation_store

    @cached_property
    def refresh_store(self) -> SqlRefreshStore:
        return SqlRefreshStore(self.shared.container.session_factory, self.scope)
