from dataclasses import dataclass, field
from uuid import UUID

from app.core.config import CompetitorSettings
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile
from app.domain.topics import TopicRef
from app.pipeline.contracts import BudgetLedger, TransientError
from app.ports.clock import Clock
from app.ports.locks import LockManager
from app.services.competitors import registration  # noqa: F401  (registers sources, resolvers and rules)
from app.services.competitors.context import DiscoveryContext, DiscoveryMeter, DiscoveryTrigger
from app.services.competitors.inputs import buyer_question_texts
from app.services.competitors.model import DiscoveryResult, UserCompetitorInput
from app.services.competitors.repository import CompetitorRepository
from app.services.competitors.service import CompetitorDiscoveryService, DiscoveryDeps

LOCK_WAIT_S = 30.0


@dataclass(frozen=True)
class DiscoveryRequest:
    scope: TenantScope
    trigger: DiscoveryTrigger
    profile: BusinessProfile
    pipeline_run_id: UUID | None = None
    own_channel_ids: list[str] = field(default_factory=list)
    topic_refs: list[TopicRef] = field(default_factory=list)
    user_inputs: list[UserCompetitorInput] = field(default_factory=list)
    enabled_sources: frozenset[str] | None = None
    ledger: BudgetLedger | None = None


class DiscoveryRunner:
    """one locked discovery per client: record the run, discover, persist candidates and usage."""

    def __init__(
        self,
        session_factory: SessionFactory,
        locks: LockManager,
        deps: DiscoveryDeps,
        settings: CompetitorSettings,
        clock: Clock,
    ) -> None:
        self.session_factory = session_factory
        self.locks = locks
        self.deps = deps
        self.settings = settings
        self.clock = clock

    async def run(self, request: DiscoveryRequest) -> DiscoveryResult:
        key = f"competitors:discover:{request.scope.client_id}"
        try:
            async with self.locks.acquire(key, ttl_s=self.settings.lock_ttl_s, wait_s=LOCK_WAIT_S):
                return await self._run_locked(request)
        except TimeoutError as error:
            raise TransientError(
                "competitor discovery is already running for this client", retry_after_s=30
            ) from error

    async def _run_locked(self, request: DiscoveryRequest) -> DiscoveryResult:
        now = self.clock.now()
        async with self.session_factory() as session:
            repository = CompetitorRepository(session, request.scope)
            discovery_run_id = await repository.runs.start(request.trigger, request.pipeline_run_id, now)
            known = await repository.known()
            questions = await buyer_question_texts(session, request.scope)
            await session.commit()
        ctx = DiscoveryContext(
            workspace_id=request.scope.workspace_id,
            client_id=request.scope.client_id,
            discovery_run_id=discovery_run_id,
            pipeline_run_id=request.pipeline_run_id,
            trigger=request.trigger,
            profile=request.profile,
            settings=self.settings,
            meter=DiscoveryMeter(self.settings.budget, self.settings.max_resolution_searches, request.ledger),
            now=now,
            own_channel_ids=list(request.own_channel_ids),
            topic_refs=list(request.topic_refs),
            buyer_questions=questions,
            user_inputs=list(request.user_inputs),
            known=known,
            enabled_sources=request.enabled_sources,
        )
        service = CompetitorDiscoveryService(self.deps, self.settings)
        try:
            result = await service.discover(ctx)
        except Exception as error:
            await self._finish(
                request.scope, discovery_run_id, ctx, status="failed", error=type(error).__name__
            )
            raise
        async with self.session_factory() as session:
            repository = CompetitorRepository(session, request.scope)
            await repository.save_result(result, now)
            await repository.runs.finish(
                discovery_run_id,
                status="partial" if result.partial else "completed",
                usage=result.usage,
                warnings=result.warnings,
                finished_at=self.clock.now(),
            )
            await session.commit()
        return result

    async def _finish(
        self, scope: TenantScope, discovery_run_id: UUID, ctx: DiscoveryContext, *, status: str, error: str
    ) -> None:
        async with self.session_factory() as session:
            await CompetitorRepository(session, scope).runs.finish(
                discovery_run_id,
                status=status,
                usage=ctx.meter.usage(),
                warnings=ctx.warnings,
                finished_at=self.clock.now(),
                error=error,
            )
            await session.commit()
