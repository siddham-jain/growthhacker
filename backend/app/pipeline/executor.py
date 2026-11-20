import asyncio
import contextlib
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.clock import FrozenClock
from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.domain.runs import RunEventType, RunKind, RunStatus, RunTrigger
from app.models.runs import PipelineRun
from app.pipeline import progress_messages as messages
from app.pipeline.artifacts import ArtifactExpiry, ArtifactMissing, PgArtifactStore
from app.pipeline.checkpoints import CheckpointStore, LeaseLost
from app.pipeline.context import RunBudgetLedger, RunCancellation
from app.pipeline.contracts import RunBudget, RunConfigSnapshot, TransientError, UsageLedger
from app.pipeline.deps import PipelineDeps
from app.pipeline.plan import RunError, RunOutcome, RunParams, RunPlan, RunTimings
from app.pipeline.progress import RUN_TERMINAL_EVENT, EventLog, EventPublisher, RunProgress, cancel_key
from app.pipeline.registry import PipelineDefinition
from app.pipeline.runner import DagRunner, tracker_for
from app.pipeline.runs import RunService, RunServiceError
from app.pipeline.setup import SetupInvalid, SetupNotReady, SetupNotWired, SetupService
from app.pipeline.stages.finalize import PUBLISHED_RESULTS
from app.pipeline.stages.orchestration import ONBOARDING_SUMMARY, awaiting_message

logger = get_logger(__name__)


class RunExecutor:
    """one attempt of one run: claim the lease, run the dag, publish the terminal state."""

    def __init__(self, *, container: Any, runs: RunService, publisher: EventPublisher) -> None:
        self._container = container
        self._runs = runs
        self._publisher = publisher
        self._settings = container.settings
        self._deps = PipelineDeps(container)

    async def execute(self, run_id: UUID, attempt: int, owner: str) -> RunStatus | None:
        run = await self._runs.claim(run_id, attempt, owner)
        if run is None:
            logger.info("run.claim_skipped", run_id=str(run_id), attempt=attempt)
            return None
        try:
            definition = self._runs.definition(run)
        except LookupError as error:
            await self._runs.fail_unclaimed(
                run, RunError(code="pipeline_unavailable", message=str(error)[:500])
            )
            return RunStatus.failed
        scope = TenantScope(workspace_id=run.workspace_id, client_id=run.client_id)
        artifacts = PgArtifactStore(
            self._container.session_factory,
            scope,
            self._container.clock,
            ArtifactExpiry(
                api_data_days=self._settings.policy.retention_days,
                other_days=self._settings.retention.run_artifacts_ttl_days,
            ),
        )
        progress = RunProgress(
            run_id=run.id,
            client_id=run.client_id,
            log=EventLog(self._container.session_factory, scope),
            publisher=self._publisher,
            prefix=self._settings.redis.key_prefix,
            settings=self._settings.pipeline,
            tracker=tracker_for(definition, self._runs.catalog),
        )
        cancel = RunCancellation()
        if run.cancel_requested_at is not None:
            cancel.request("user")
        await progress.emit(
            RunEventType.run_started,
            messages.run_started(RunKind(run.kind), attempt),
            status=RunStatus.running,
        )
        supervisor = asyncio.create_task(self._supervise(run, owner, cancel, progress))
        try:
            params = RunParams.model_validate(run.params or {})
            outcome = None
            if params.confirm_setup and not params.setup_prepared:
                run, outcome = await self._confirm_setup(run, scope, owner, progress)
            if outcome is None:
                outcome = await self._run_dag(run, scope, definition, owner, artifacts, progress, cancel)
        except asyncio.CancelledError:
            await asyncio.shield(self._interrupted(run, owner, cancel, progress))
            raise
        except LeaseLost:
            logger.warning("run.lease_lost", run_id=str(run.id), owner=owner)
            await progress.aclose()
            return None
        finally:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        final = await self._runs.finish(run, owner, outcome)
        if final is None:
            await progress.aclose()
            return None
        await self._announce(run, definition, final, artifacts, progress)
        await progress.aclose()
        if final in {RunStatus.succeeded, RunStatus.partial} and definition.name == "onboarding":
            await self._auto_confirm(scope, run)
        return final

    async def _run_dag(
        self,
        run: PipelineRun,
        scope: TenantScope,
        definition: PipelineDefinition,
        owner: str,
        artifacts: PgArtifactStore,
        progress: RunProgress,
        cancel: RunCancellation,
    ) -> RunOutcome:
        plan = self._plan(run, scope, definition)
        runner = DagRunner(
            catalog=self._runs.catalog,
            artifacts=artifacts,
            checkpoints=CheckpointStore(self._container.session_factory, scope, artifacts, owner),
            settings=self._settings.pipeline,
            quota_tz=ZoneInfo(self._settings.quota.timezone),
            clock=self._container.clock,
            business_clock=FrozenClock(run.as_of) if plan.kind is RunKind.backtest else self._container.clock,
            deps=await self._deps.for_run(scope, plan.kind),
        )
        ledger = RunBudgetLedger(plan.budget, plan.prior_usage)
        return await runner.execute(plan, progress, cancel, ledger)

    async def _confirm_setup(
        self, run: PipelineRun, scope: TenantScope, owner: str, progress: RunProgress
    ) -> tuple[PipelineRun, RunOutcome | None]:
        await progress.emit(RunEventType.run_progress, messages.setup_confirming())
        try:
            snapshot = await self._setup().prepare_run(scope, run.window_days)
        except Exception as error:
            # any confirm failure ends this run visibly; the client goes back to awaiting confirmation
            logger.warning("run.confirm_setup_failed", run_id=str(run.id), error=type(error).__name__)
            failure = setup_error(error)
            await progress.emit(
                RunEventType.run_progress, messages.setup_failed(failure.message), level="error"
            )
            return run, RunOutcome(
                status=RunStatus.failed,
                error=failure,
                notices=list(progress.notices),
                usage=UsageLedger(),
                stages={},
                timings=RunTimings(),
            )
        prepared = await self._runs.record_setup(run, owner, snapshot)
        if prepared is None:
            raise LeaseLost(f"run {run.id} lost its lease while confirming the setup")
        return prepared, None

    def _setup(self) -> SetupService:
        return SetupService.from_container(self._container, self._runs)

    def _plan(self, run: PipelineRun, scope: TenantScope, definition: PipelineDefinition) -> RunPlan:
        return RunPlan(
            run_id=run.id,
            scope=scope,
            kind=RunKind(run.kind),
            trigger=RunTrigger(run.trigger),
            attempt=run.attempt,
            as_of=run.as_of,
            snapshot=RunConfigSnapshot.model_validate(run.config_snapshot),
            params=RunParams.model_validate(run.params or {}),
            definition=definition,
            budget=RunBudget.model_validate(run.budget),
            prior_usage=UsageLedger.model_validate(run.usage) if run.usage else UsageLedger(),
        )

    async def _announce(
        self,
        run: PipelineRun,
        definition: PipelineDefinition,
        final: RunStatus,
        artifacts: PgArtifactStore,
        progress: RunProgress,
    ) -> None:
        succeeded = final in {RunStatus.succeeded, RunStatus.partial}
        if succeeded and definition.name == "onboarding":
            with contextlib.suppress(ArtifactMissing):
                summary = await artifacts.get(run.id, ONBOARDING_SUMMARY)
                await progress.emit(RunEventType.run_awaiting_confirmation, awaiting_message(summary))
        if succeeded:
            with contextlib.suppress(ArtifactMissing):
                published = await artifacts.get(run.id, PUBLISHED_RESULTS)
                if published.windows:
                    await progress.emit(
                        RunEventType.results_published, messages.results_published(published.windows)
                    )
        await progress.emit(
            RUN_TERMINAL_EVENT[final],
            messages.run_finished(RunKind(run.kind), final),
            status=final,
            level="error" if final is RunStatus.failed else "info",
        )

    async def _interrupted(
        self, run: PipelineRun, owner: str, cancel: RunCancellation, progress: RunProgress
    ) -> None:
        if cancel.is_cancelled():
            outcome = RunOutcome(
                status=RunStatus.cancelled,
                error=None,
                notices=list(progress.notices),
                usage=UsageLedger(),
                stages={},
                timings=RunTimings(),
            )
            final = await self._runs.finish(run, owner, outcome)
            if final is not None:
                await progress.emit(
                    RunEventType.run_cancelled,
                    messages.run_finished(RunKind(run.kind), RunStatus.cancelled),
                    status=RunStatus.cancelled,
                )
        else:
            await self._runs.release(run.id, owner)
        await progress.aclose()

    async def _supervise(
        self, run: PipelineRun, owner: str, cancel: RunCancellation, progress: RunProgress
    ) -> None:
        pipeline = self._settings.pipeline
        key = cancel_key(self._settings.redis.key_prefix, run.id)
        loop = asyncio.get_running_loop()
        next_heartbeat = loop.time() + pipeline.heartbeat_s
        failures = 0
        while True:
            await asyncio.sleep(pipeline.cancel_poll_s)
            with contextlib.suppress(RedisError, OSError):
                if await self._container.redis.get(key):
                    cancel.request("user")
            if loop.time() < next_heartbeat:
                continue
            next_heartbeat = loop.time() + pipeline.heartbeat_s
            try:
                beat = await self._runs.heartbeat(run.id, owner)
            except (SQLAlchemyError, OSError) as error:
                failures += 1
                logger.warning(
                    "run.heartbeat_failed", run_id=str(run.id), failures=failures, error=type(error).__name__
                )
                continue
            failures = 0
            if not beat.alive:
                logger.warning("run.lease_lost", run_id=str(run.id))
                return
            if beat.cancel_requested:
                cancel.request("user")
            if beat.client_deleting:
                cancel.request("client_deleted")
            await progress.tick()

    async def _auto_confirm(self, scope: TenantScope, run: PipelineRun) -> None:
        try:
            await self._setup().auto_confirm(scope, requested_by="auto_confirm")
        except (SetupInvalid, SetupNotReady, SetupNotWired, RunServiceError, LookupError) as error:
            # the onboarding run already finished; the client simply waits for a manual confirmation
            logger.warning("run.auto_confirm_skipped", run_id=str(run.id), reason=str(error)[:200])


def setup_error(error: Exception) -> RunError:
    if isinstance(error, SetupInvalid):
        code = error.problems[0].split(":", 1)[0]
    else:
        declared = getattr(error, "code", None)
        code = declared if isinstance(declared, str) else "setup_failed"
    return RunError(
        code=code,
        message=(str(error) or type(error).__name__)[:500],
        retryable=isinstance(error, TransientError | TimeoutError),
    )
