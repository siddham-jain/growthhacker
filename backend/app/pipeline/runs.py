from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.domain.runs import (
    ACTIVE_RUN_STATUSES,
    ClientLifecycle,
    RunEventType,
    RunKind,
    RunStatus,
    RunTrigger,
    StageStatus,
)
from app.models.ledgers import ProviderUsageLedger
from app.models.runs import ClientSchedule, PipelineRun, StageRun
from app.models.tenancy import Client
from app.pipeline import progress_messages as messages
from app.pipeline.config_source import RunConfigSource
from app.pipeline.contracts import RunConfigSnapshot, UsageLedger
from app.pipeline.definitions import default_pipeline
from app.pipeline.fingerprint import sha256_hex
from app.pipeline.lifecycle import ClientLifecycleMachine, RunStateMachine
from app.pipeline.plan import RunError, RunOutcome, RunParams
from app.pipeline.progress import (
    LIFECYCLE_EVENT_TYPES,
    RUN_TERMINAL_EVENT,
    EventLog,
    EventPublisher,
    StoredEvent,
    cancel_key,
    client_channel,
    run_channel,
)
from app.pipeline.registry import PipelineCatalog, PipelineDefinition
from app.ports.jobs import JobQueue

logger = get_logger(__name__)

ACTIVE_VALUES = tuple(status.value for status in ACTIVE_RUN_STATUSES)


class RunServiceError(Exception):
    code = "run_error"

    def __init__(self, message: str, *, run_id: UUID | None = None, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.retry_after_s = retry_after_s


class RunNotFound(RunServiceError):
    code = "not_found"


class ClientNotFound(RunServiceError):
    code = "not_found"


class ClientDeleting(RunServiceError):
    code = "client_deleting"


class RunInProgress(RunServiceError):
    code = "run_in_progress"


class RunCancellingConflict(RunServiceError):
    code = "run_cancelling"


class RunNotCancellable(RunServiceError):
    code = "run_not_cancellable"


class RunNotRetryable(RunServiceError):
    code = "run_not_retryable"


class WorkspaceBusy(RunServiceError):
    code = "workspace_busy"


class ScheduleSkipped(RunServiceError):
    code = "schedule_skipped"


@dataclass(frozen=True)
class RunRequest:
    kind: RunKind
    trigger: RunTrigger
    pipeline: str | None = None
    window_days: int | None = None
    force: bool = False
    from_stage: str | None = None
    resume_of: UUID | None = None
    idempotency_key: str | None = None
    requested_by: str | None = None
    confirm_setup: bool = False
    # backtests only: the frozen point in time the run reasons about
    as_of: datetime | None = None

    @property
    def pipeline_name(self) -> str:
        return self.pipeline or default_pipeline(self.kind)


@dataclass(frozen=True)
class RunRequestResult:
    run: PipelineRun
    created: bool
    follow_up_requested: bool = False
    superseded_run_id: UUID | None = None

    @property
    def deduplicated(self) -> bool:
        return not self.created


Decision = Literal["join", "supersede", "follow_up", "cancelling", "conflict"]


def dedupe_decision(
    *,
    active_kind: RunKind,
    active_status: RunStatus,
    active_config_hash: str,
    requested_kind: RunKind,
    requested_config_hash: str,
) -> Decision:
    """what a new request does when the client already has an active run (plan 11 §5.7)."""
    if active_status is RunStatus.cancelling:
        return "cancelling"
    changed = active_config_hash != requested_config_hash
    if requested_kind is RunKind.analysis:
        if active_kind is RunKind.daily_refresh and active_status is RunStatus.queued:
            return "supersede"
        if active_kind in {RunKind.analysis, RunKind.daily_refresh} and changed:
            return "follow_up" if active_status is RunStatus.running else "supersede"
    if active_kind is requested_kind:
        return "join"
    if requested_kind is RunKind.daily_refresh and active_kind is RunKind.analysis:
        return "join"
    return "conflict"


def confirm_decision(*, active_kind: RunKind, active_status: RunStatus, active_confirms: bool) -> Decision:
    """a confirm-setup request never joins a run that would skip the setup freeze."""
    if active_status is RunStatus.cancelling:
        return "cancelling"
    if active_confirms:
        return "join"
    if active_status is RunStatus.queued and active_kind in {RunKind.analysis, RunKind.daily_refresh}:
        return "supersede"
    return "conflict"


def job_id(run_id: UUID, attempt: int) -> str:
    return f"run:{run_id}:{attempt}"


@dataclass(frozen=True)
class Heartbeat:
    alive: bool
    cancel_requested: bool = False
    client_deleting: bool = False


class RunService:
    """the only writer of pipeline_runs status; every transition is a compare-and-set update."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        catalog: PipelineCatalog,
        settings: Settings,
        config_source: RunConfigSource,
        clock: Clock,
        job_queue: JobQueue | None,
        publisher: EventPublisher,
        redis=None,
    ) -> None:
        self._session_factory = session_factory
        self._catalog = catalog
        self._settings = settings
        self._config_source = config_source
        self._clock = clock
        self._job_queue = job_queue
        self._publisher = publisher
        self._redis = redis

    @property
    def catalog(self) -> PipelineCatalog:
        return self._catalog

    def definition(self, run: PipelineRun) -> PipelineDefinition:
        params = RunParams.model_validate(run.params or {})
        return self._catalog.require(params.pipeline or default_pipeline(RunKind(run.kind)))

    def queue_name(self, role: str) -> str:
        workers = self._settings.workers
        return workers.interactive_queue if role == "interactive" else workers.batch_queue

    async def request_run(self, scope: TenantScope, request: RunRequest) -> RunRequestResult:
        if scope.client_id is None:
            raise ValueError("request_run needs a client-bound scope")
        definition = self._catalog.require(request.pipeline_name)
        for attempt in range(2):
            try:
                result = await self._request_in_txn(scope, request, definition)
                break
            except IntegrityError:
                # lost the race on the one-active-run index; the second pass sees the winner
                if attempt == 1:
                    raise
        if result.created:
            await self._after_create(scope, result)
        if result.superseded_run_id is not None:
            await self._emit(
                scope,
                result.superseded_run_id,
                RunEventType.run_superseded,
                messages.run_finished(RunKind(result.run.kind), RunStatus.superseded),
                RunStatus.superseded,
            )
        return result

    async def _request_in_txn(
        self, scope: TenantScope, request: RunRequest, definition: PipelineDefinition
    ) -> RunRequestResult:
        async with self._session_factory() as session, session.begin():
            client = await self._lock_client(session, scope)
            lifecycle = ClientLifecycle(client.lifecycle)
            if lifecycle is ClientLifecycle.deleting:
                raise ClientDeleting("this client is being deleted")
            if request.trigger is RunTrigger.schedule and lifecycle in {
                ClientLifecycle.paused,
                ClientLifecycle.archived,
            }:
                raise ScheduleSkipped("client is paused")
            if request.idempotency_key is not None:
                existing = await self._by_idempotency_key(session, scope, request.idempotency_key)
                if existing is not None:
                    return RunRequestResult(run=existing, created=False)
            snapshot = await self._config_source.snapshot(session, client, request.window_days)
            active = await self._active(session, scope)
            superseded: UUID | None = None
            if active is not None:
                if request.confirm_setup:
                    decision = confirm_decision(
                        active_kind=RunKind(active.kind),
                        active_status=RunStatus(active.status),
                        active_confirms=RunParams.model_validate(active.params or {}).confirm_setup,
                    )
                else:
                    decision = dedupe_decision(
                        active_kind=RunKind(active.kind),
                        active_status=RunStatus(active.status),
                        active_config_hash=active.config_hash,
                        requested_kind=request.kind,
                        requested_config_hash=snapshot.config_hash,
                    )
                if decision == "join":
                    return RunRequestResult(run=active, created=False)
                if decision == "follow_up":
                    active.follow_up_requested = True
                    return RunRequestResult(run=active, created=False, follow_up_requested=True)
                if decision == "cancelling":
                    raise RunCancellingConflict(
                        "the previous run is stopping", run_id=active.id, retry_after_s=5
                    )
                if decision == "conflict":
                    raise RunInProgress(f"a {active.kind} run is already active", run_id=active.id)
                await self._supersede(session, active)
                superseded = active.id
            await self._check_workspace_capacity(session, scope, request.trigger)
            resume_of = await self._resume_source(session, scope, request)
            params = RunParams(
                pipeline=request.pipeline,
                force=request.force,
                from_stage=request.from_stage if resume_of is not None else None,
                confirm_setup=request.confirm_setup,
            )
            budget = self._settings.pipeline.budget(request.kind.value)
            role = definition.queue_for(request.trigger)
            if request.as_of is not None and request.kind is not RunKind.backtest:
                raise ValueError("only backtest runs take an explicit as_of")
            run = PipelineRun(
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                kind=request.kind.value,
                trigger=request.trigger.value,
                status=RunStatus.queued.value,
                resume_of_run_id=resume_of,
                window_days=snapshot.window_days,
                config_snapshot=snapshot.model_dump(mode="json"),
                config_hash=snapshot.config_hash,
                params=params.model_dump(mode="json"),
                params_hash=sha256_hex(params.model_dump(mode="json")),
                queue=self.queue_name(role),
                priority=definition.priority,
                budget=budget.model_dump(mode="json"),
                idempotency_key=request.idempotency_key,
                requested_by=request.requested_by,
            )
            if request.as_of is not None:
                run.as_of = request.as_of
            session.add(run)
            await session.flush()
            if superseded is not None:
                await session.execute(
                    update(PipelineRun)
                    .where(PipelineRun.id == superseded)
                    .values(superseded_by_run_id=run.id)
                )
            await session.refresh(run)
            return RunRequestResult(run=run, created=True, superseded_run_id=superseded)

    async def _after_create(self, scope: TenantScope, result: RunRequestResult) -> None:
        run = result.run
        await self._emit(
            scope, run.id, RunEventType.run_queued, messages.run_queued(RunKind(run.kind)), RunStatus.queued
        )
        if result.superseded_run_id is not None:
            await self._abort_job(scope, result.superseded_run_id)
        await self.enqueue(run)

    async def enqueue(self, run: PipelineRun) -> str | None:
        if self._job_queue is None:
            return None
        try:
            enqueued = await self._job_queue.enqueue_run(run.id, run.attempt, run.queue)
        except (RedisError, OSError) as error:
            # the reconciler re-enqueues queued runs whose job is missing
            logger.warning("runs.enqueue_failed", run_id=str(run.id), error=type(error).__name__)
            return None
        identifier = enqueued or job_id(run.id, run.attempt)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(PipelineRun).where(PipelineRun.id == run.id).values(arq_job_id=identifier)
            )
        run.arq_job_id = identifier
        return enqueued

    async def record_setup(
        self, run: PipelineRun, owner: str, snapshot: RunConfigSnapshot
    ) -> PipelineRun | None:
        """stores the inputs a confirm-setup run froze in the worker; the dag then runs on them."""
        params = RunParams.model_validate(run.params or {}).model_copy(update={"setup_prepared": True})
        async with self._session_factory() as session, session.begin():
            statement = (
                update(PipelineRun)
                .where(
                    PipelineRun.id == run.id,
                    PipelineRun.lease_owner == owner,
                    PipelineRun.status.in_((RunStatus.running.value, RunStatus.cancelling.value)),
                )
                .values(
                    config_snapshot=snapshot.model_dump(mode="json"),
                    config_hash=snapshot.config_hash,
                    window_days=snapshot.window_days,
                    params=params.model_dump(mode="json"),
                )
                .returning(PipelineRun)
            )
            return (await session.execute(statement)).scalar_one_or_none()

    async def cancel(self, scope: TenantScope, run_id: UUID, reason: str | None = None) -> PipelineRun:
        async with self._session_factory() as session, session.begin():
            run = await self._scoped_run(session, scope, run_id, lock=True)
            status = RunStatus(run.status)
            if RunStateMachine.is_terminal(status):
                raise RunNotCancellable("this run already finished", run_id=run.id)
            now = await self._db_now(session)
            if status is RunStatus.queued:
                run.status = RunStatus.cancelled.value
                run.finished_at = now
                run.cancel_requested_at = now
                run.error = RunError(
                    code="cancelled", message=(reason or "cancelled by user")[:500]
                ).model_dump()
            elif status is RunStatus.running:
                run.status = RunStatus.cancelling.value
                run.cancel_requested_at = now
            await session.flush()
            await session.refresh(run)
        if self._redis is not None:
            try:
                await self._redis.set(cancel_key(self._settings.redis.key_prefix, run.id), "1", ex=3600)
            except (RedisError, OSError) as error:
                logger.warning("runs.cancel_flag_failed", run_id=str(run.id), error=type(error).__name__)
        if status is RunStatus.queued:
            await self._abort_job(scope, run.id)
            await self._emit(
                scope.for_client(run.client_id),
                run.id,
                RunEventType.run_cancelled,
                messages.run_finished(RunKind(run.kind), RunStatus.cancelled),
                RunStatus.cancelled,
            )
        return run

    async def retry(
        self, scope: TenantScope, run_id: UUID, from_stage: str | None = None
    ) -> RunRequestResult:
        async with self._session_factory() as session:
            source = await self._scoped_run(session, scope, run_id)
        if RunStatus(source.status) not in {RunStatus.failed, RunStatus.partial, RunStatus.cancelled}:
            raise RunNotRetryable("only failed, partial or cancelled runs can be retried", run_id=source.id)
        params = RunParams.model_validate(source.params or {})
        return await self.request_run(
            scope.for_client(source.client_id),
            RunRequest(
                kind=RunKind(source.kind),
                trigger=RunTrigger.resume,
                pipeline=params.pipeline,
                window_days=source.window_days,
                from_stage=from_stage,
                resume_of=source.id,
                confirm_setup=params.confirm_setup and not params.setup_prepared,
            ),
        )

    async def active_run(self, scope: TenantScope) -> PipelineRun | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(PipelineRun).where(
                        PipelineRun.workspace_id == scope.workspace_id,
                        PipelineRun.client_id == scope.client_id,
                        PipelineRun.status.in_(ACTIVE_VALUES),
                    )
                )
            ).scalar_one_or_none()

    async def get(self, scope: TenantScope, run_id: UUID) -> PipelineRun:
        async with self._session_factory() as session:
            return await self._scoped_run(session, scope, run_id)

    async def claim(self, run_id: UUID, attempt: int, owner: str) -> PipelineRun | None:
        lease = timedelta(seconds=self._settings.pipeline.lease_s)
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            statement = (
                update(PipelineRun)
                .where(
                    PipelineRun.id == run_id,
                    PipelineRun.status.in_((RunStatus.queued.value, RunStatus.running.value)),
                    (PipelineRun.lease_expires_at.is_(None))
                    | (PipelineRun.lease_expires_at < now)
                    | (PipelineRun.lease_owner == owner),
                    PipelineRun.attempt <= attempt,
                )
                .values(
                    lease_owner=owner,
                    lease_expires_at=now + lease,
                    attempt=attempt,
                    status=RunStatus.running.value,
                    started_at=func.coalesce(PipelineRun.started_at, now),
                )
                .returning(PipelineRun)
            )
            run = (await session.execute(statement)).scalar_one_or_none()
            if run is None:
                return None
            try:
                definition = self.definition(run)
            except LookupError:
                return run
            if definition.drives_lifecycle:
                await self._move_client(
                    session,
                    run,
                    lambda current: ClientLifecycleMachine.on_run_started(current, RunKind(run.kind)),
                )
            return run

    async def heartbeat(self, run_id: UUID, owner: str) -> Heartbeat:
        lease = timedelta(seconds=self._settings.pipeline.lease_s)
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            statement = (
                update(PipelineRun)
                .where(
                    PipelineRun.id == run_id,
                    PipelineRun.lease_owner == owner,
                    PipelineRun.status.in_((RunStatus.running.value, RunStatus.cancelling.value)),
                )
                .values(lease_expires_at=now + lease)
                .returning(PipelineRun.status, PipelineRun.cancel_requested_at, PipelineRun.client_id)
            )
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return Heartbeat(alive=False)
            lifecycle = (
                await session.execute(select(Client.lifecycle).where(Client.id == row.client_id))
            ).scalar_one_or_none()
        return Heartbeat(
            alive=True,
            cancel_requested=row.cancel_requested_at is not None,
            client_deleting=lifecycle == ClientLifecycle.deleting.value,
        )

    async def release(self, run_id: UUID, owner: str) -> None:
        # an interrupted attempt hands the run back; the next claim (or the reconciler) resumes it
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id, PipelineRun.lease_owner == owner)
                .values(lease_expires_at=now - timedelta(seconds=1), current_stages=[])
            )

    async def finish(self, run: PipelineRun, owner: str, outcome: RunOutcome) -> RunStatus | None:
        target = outcome.status
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            values = {
                "status": target.value,
                "finished_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "current_stages": [],
                "notices": [notice.model_dump(mode="json") for notice in outcome.notices],
                "usage": (await _with_llm_ledger(session, run.id, outcome.usage)).model_dump(mode="json"),
                "timings": {
                    **(run.timings or {}),
                    **outcome.timings.model_dump(mode="json", exclude_none=True),
                    "queued_ms": _ms(run.started_at, run.queued_at),
                },
                "error": None if outcome.error is None else outcome.error.model_dump(mode="json"),
            }
            if target in {RunStatus.succeeded, RunStatus.partial}:
                values["progress_pct"] = 100.0
            finished = await self._transition(session, run.id, owner, target, values)
            if finished is None and target is not RunStatus.cancelled:
                values.update(status=RunStatus.cancelled.value, error=None)
                finished = await self._transition(session, run.id, owner, RunStatus.cancelled, values)
            if finished is None:
                return None
            final = RunStatus(finished.status)
            definition = self.definition(finished)
            if definition.drives_lifecycle:
                await self._move_client(
                    session,
                    finished,
                    lambda current: ClientLifecycleMachine.on_run_finished(
                        current, RunKind(finished.kind), final
                    ),
                )
            await self._update_schedule(session, finished, final, now)
        if final in {RunStatus.succeeded, RunStatus.partial} and finished.follow_up_requested:
            await self._request_follow_up(finished)
        return final

    async def fail_unclaimed(self, run: PipelineRun, error: RunError) -> None:
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run.id, PipelineRun.status.in_(ACTIVE_VALUES))
                .values(
                    status=RunStatus.failed.value,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    error=error.model_dump(mode="json"),
                )
            )
        await self._emit(
            TenantScope(workspace_id=run.workspace_id, client_id=run.client_id),
            run.id,
            RunEventType.run_failed,
            messages.run_finished(RunKind(run.kind), RunStatus.failed),
            RunStatus.failed,
            level="error",
        )

    async def _transition(
        self, session: AsyncSession, run_id: UUID, owner: str, target: RunStatus, values: dict
    ) -> PipelineRun | None:
        statement = (
            update(PipelineRun)
            .where(
                PipelineRun.id == run_id,
                PipelineRun.lease_owner == owner,
                PipelineRun.status.in_(RunStateMachine.sources(target)),
            )
            .values(**values)
            .returning(PipelineRun)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _request_follow_up(self, run: PipelineRun) -> None:
        scope = TenantScope(workspace_id=run.workspace_id, client_id=run.client_id)
        try:
            await self.request_run(scope, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.system))
        except (RunServiceError, LookupError) as error:
            logger.warning("runs.follow_up_skipped", run_id=str(run.id), reason=str(error))

    async def _update_schedule(self, session: AsyncSession, run: PipelineRun, status: RunStatus, now) -> None:
        if run.kind not in {RunKind.daily_refresh.value, RunKind.news_scan.value}:
            return
        values: dict = {"last_run_id": run.id}
        if status in {RunStatus.succeeded, RunStatus.partial}:
            values.update(last_success_at=now, failure_streak=0)
        elif status is RunStatus.failed:
            values["failure_streak"] = ClientSchedule.failure_streak + 1
            if (run.error or {}).get("code") == "poison":
                values["circuit_open_until"] = now + timedelta(hours=24)
        await session.execute(
            update(ClientSchedule)
            .where(ClientSchedule.client_id == run.client_id, ClientSchedule.kind == run.kind)
            .values(**values)
        )

    async def _move_client(self, session: AsyncSession, run: PipelineRun, decide) -> None:
        current = (
            await session.execute(
                select(Client.lifecycle).where(Client.id == run.client_id).with_for_update(key_share=True)
            )
        ).scalar_one_or_none()
        if current is None:
            return
        target = decide(ClientLifecycle(current))
        if target is not None:
            await session.execute(
                update(Client)
                .where(Client.id == run.client_id, Client.lifecycle == current)
                .values(lifecycle=target.value, updated_at=func.now())
            )

    async def _supersede(self, session: AsyncSession, active: PipelineRun) -> None:
        now = await self._db_now(session)
        result = await session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == active.id, PipelineRun.status.in_(ACTIVE_VALUES))
            .values(status=RunStatus.superseded.value, finished_at=now, cancel_requested_at=now)
        )
        if result.rowcount != 1:
            raise RunInProgress("the active run changed while superseding", run_id=active.id)
        await session.flush()

    async def _check_workspace_capacity(
        self, session: AsyncSession, scope: TenantScope, trigger: RunTrigger
    ) -> None:
        pipeline = self._settings.pipeline
        cap = (
            pipeline.max_active_runs_per_workspace
            if trigger is RunTrigger.schedule
            else pipeline.max_active_runs_per_workspace_user
        )
        active = (
            await session.execute(
                select(func.count()).where(
                    PipelineRun.workspace_id == scope.workspace_id, PipelineRun.status.in_(ACTIVE_VALUES)
                )
            )
        ).scalar_one()
        if active >= cap:
            error = ScheduleSkipped if trigger is RunTrigger.schedule else WorkspaceBusy
            raise error(f"workspace already has {active} active runs", retry_after_s=30)

    async def _resume_source(
        self, session: AsyncSession, scope: TenantScope, request: RunRequest
    ) -> UUID | None:
        if request.resume_of is None:
            return None
        source = await self._scoped_run(session, scope, request.resume_of)
        now = await self._db_now(session)
        # stale evidence is never stitched together: an old source starts a fresh run instead
        if now - source.created_at > timedelta(hours=self._settings.pipeline.resume_max_age_h):
            return None
        return source.id

    async def _lock_client(self, session: AsyncSession, scope: TenantScope) -> Client:
        statement = (
            select(Client)
            .where(
                Client.id == scope.client_id,
                Client.workspace_id == scope.workspace_id,
                Client.deleted_at.is_(None),
            )
            .with_for_update(key_share=True)
        )
        client = (await session.execute(statement)).scalar_one_or_none()
        if client is None:
            raise ClientNotFound("client not found")
        return client

    async def _active(self, session: AsyncSession, scope: TenantScope) -> PipelineRun | None:
        statement = (
            select(PipelineRun)
            .where(
                PipelineRun.workspace_id == scope.workspace_id,
                PipelineRun.client_id == scope.client_id,
                PipelineRun.status.in_(ACTIVE_VALUES),
            )
            .with_for_update(key_share=True)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _by_idempotency_key(
        self, session: AsyncSession, scope: TenantScope, key: str
    ) -> PipelineRun | None:
        statement = select(PipelineRun).where(
            PipelineRun.workspace_id == scope.workspace_id, PipelineRun.idempotency_key == key
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def _scoped_run(
        self, session: AsyncSession, scope: TenantScope, run_id: UUID, *, lock: bool = False
    ) -> PipelineRun:
        statement = select(PipelineRun).where(
            PipelineRun.id == run_id, PipelineRun.workspace_id == scope.workspace_id
        )
        if scope.client_id is not None:
            statement = statement.where(PipelineRun.client_id == scope.client_id)
        if lock:
            statement = statement.with_for_update(key_share=True)
        run = (await session.execute(statement)).scalar_one_or_none()
        if run is None:
            raise RunNotFound("run not found")
        return run

    async def _abort_job(self, scope: TenantScope, run_id: UUID) -> None:
        if self._job_queue is None:
            return
        async with self._session_factory() as session:
            identifier = (
                await session.execute(
                    select(PipelineRun.arq_job_id).where(
                        PipelineRun.id == run_id, PipelineRun.workspace_id == scope.workspace_id
                    )
                )
            ).scalar_one_or_none()
        if identifier:
            try:
                await self._job_queue.abort(identifier, timeout_s=0)
            except (RedisError, OSError) as error:
                logger.warning("runs.abort_failed", run_id=str(run_id), error=type(error).__name__)

    async def _emit(
        self,
        scope: TenantScope,
        run_id: UUID,
        type: RunEventType,
        message: str,
        status: RunStatus,
        level: str = "info",
    ) -> list[StoredEvent]:
        # only for runs no worker is writing (queued, superseded, cancelled before start)
        log = EventLog(self._session_factory, TenantScope(workspace_id=scope.workspace_id))
        stored = await log.append(
            run_id,
            [
                {
                    "type": type.value,
                    "stage": None,
                    "step": None,
                    "status": status.value,
                    "level": level,
                    "message": message,
                    "counts": None,
                    "percent": None,
                    "stage_percent": None,
                    "payload": None,
                }
            ],
        )
        prefix = self._settings.redis.key_prefix
        for event in stored:
            message_json = event.to_message()
            await self._publisher.publish(run_channel(prefix, run_id), message_json)
            if event.event.type in LIFECYCLE_EVENT_TYPES:
                await self._publisher.publish(client_channel(prefix, event.event.client_id), message_json)
        return stored

    async def stale_leases(self, limit: int = 100) -> Sequence[PipelineRun]:
        grace = timedelta(seconds=self._settings.pipeline.reconcile_lease_grace_s)
        async with self._session_factory() as session:
            now = await self._db_now(session)
            statement = (
                select(PipelineRun)
                .where(
                    PipelineRun.status.in_((RunStatus.running.value, RunStatus.cancelling.value)),
                    PipelineRun.lease_owner.is_not(None),
                    PipelineRun.lease_expires_at < now - grace,
                )
                .order_by(PipelineRun.lease_expires_at)
                .limit(limit)
            )
            return (await session.execute(statement)).scalars().all()

    async def orphaned(self, limit: int = 100) -> Sequence[PipelineRun]:
        # queued or handed-back runs whose job may have been lost with redis
        older_than = timedelta(seconds=self._settings.pipeline.requeue_after_s)
        async with self._session_factory() as session:
            now = await self._db_now(session)
            statement = (
                select(PipelineRun)
                .where(
                    (
                        (PipelineRun.status == RunStatus.queued.value)
                        & (PipelineRun.queued_at < now - older_than)
                    )
                    | ((PipelineRun.status == RunStatus.running.value) & PipelineRun.lease_owner.is_(None))
                )
                .limit(limit)
            )
            return (await session.execute(statement)).scalars().all()

    async def overdue_cancels(self, limit: int = 100) -> Sequence[PipelineRun]:
        grace = timedelta(seconds=self._settings.pipeline.cancel_grace_s)
        async with self._session_factory() as session:
            now = await self._db_now(session)
            statement = (
                select(PipelineRun)
                .where(
                    PipelineRun.status == RunStatus.cancelling.value,
                    PipelineRun.cancel_requested_at < now - grace,
                )
                .limit(limit)
            )
            return (await session.execute(statement)).scalars().all()

    async def recover(self, run: PipelineRun) -> PipelineRun | None:
        """a crashed attempt: same row, attempt + 1, interrupted stages restart from checkpoints."""
        scope = TenantScope(workspace_id=run.workspace_id, client_id=run.client_id)
        if run.status == RunStatus.cancelling.value:
            await self._force_terminal(run, RunStatus.cancelled, None)
            return None
        if run.attempt >= self._settings.pipeline.max_attempts:
            error = RunError(code="poison", message="the run crashed on every attempt", retryable=False)
            await self._force_terminal(run, RunStatus.failed, error)
            return None
        async with self._session_factory() as session, session.begin():
            recovered = (
                await session.execute(
                    update(PipelineRun)
                    .where(
                        PipelineRun.id == run.id,
                        PipelineRun.attempt == run.attempt,
                        PipelineRun.status == RunStatus.running.value,
                        PipelineRun.lease_owner == run.lease_owner,
                    )
                    .values(
                        attempt=run.attempt + 1, lease_owner=None, lease_expires_at=None, current_stages=[]
                    )
                    .returning(PipelineRun)
                )
            ).scalar_one_or_none()
            if recovered is None:
                return None
            interrupted = (
                (
                    await session.execute(
                        update(StageRun)
                        .where(StageRun.run_id == run.id, StageRun.status == StageStatus.running.value)
                        .values(status=StageStatus.interrupted.value)
                        .returning(StageRun.stage)
                    )
                )
                .scalars()
                .all()
            )
        for stage in sorted(interrupted):
            await self._emit_stage(scope, run.id, stage)
        await self.enqueue(recovered)
        return recovered

    async def _force_terminal(self, run: PipelineRun, status: RunStatus, error: RunError | None) -> None:
        async with self._session_factory() as session, session.begin():
            now = await self._db_now(session)
            done = (
                await session.execute(
                    update(PipelineRun)
                    .where(PipelineRun.id == run.id, PipelineRun.status.in_(RunStateMachine.sources(status)))
                    .values(
                        status=status.value,
                        finished_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        current_stages=[],
                        error=None if error is None else error.model_dump(mode="json"),
                    )
                    .returning(PipelineRun)
                )
            ).scalar_one_or_none()
            if done is None:
                return
            await session.execute(
                update(StageRun)
                .where(StageRun.run_id == run.id, StageRun.status == StageStatus.running.value)
                .values(status=StageStatus.interrupted.value, finished_at=now)
            )
            definition = self.definition(done)
            if definition.drives_lifecycle:
                await self._move_client(
                    session,
                    done,
                    lambda current: ClientLifecycleMachine.on_run_finished(
                        current, RunKind(done.kind), status
                    ),
                )
            await self._update_schedule(session, done, status, now)
        scope = TenantScope(workspace_id=run.workspace_id, client_id=run.client_id)
        await self._emit(
            scope,
            run.id,
            RUN_TERMINAL_EVENT[status],
            messages.run_finished(RunKind(run.kind), status),
            status,
            level="error" if status is RunStatus.failed else "info",
        )

    async def _emit_stage(self, scope: TenantScope, run_id: UUID, stage: str) -> None:
        log = EventLog(self._session_factory, TenantScope(workspace_id=scope.workspace_id))
        stored = await log.append(
            run_id,
            [
                {
                    "type": RunEventType.stage_retrying.value,
                    "stage": stage,
                    "step": None,
                    "status": StageStatus.interrupted.value,
                    "level": "warning",
                    "message": messages.stage_resuming(stage),
                    "counts": None,
                    "percent": None,
                    "stage_percent": None,
                    "payload": None,
                }
            ],
        )
        for event in stored:
            await self._publisher.publish(
                run_channel(self._settings.redis.key_prefix, run_id), event.to_message()
            )

    @staticmethod
    async def _db_now(session: AsyncSession):
        return (await session.execute(text("SELECT now()"))).scalar_one()


async def _with_llm_ledger(session: AsyncSession, run_id: UUID, usage: UsageLedger) -> UsageLedger:
    # every llm call books the provider ledger, while only some stages charge the run budget
    ledger = ProviderUsageLedger
    calls, input_tokens, output_tokens, cache_read, cache_write, cost = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(ledger.input_tokens), 0),
                func.coalesce(func.sum(ledger.output_tokens), 0),
                func.coalesce(func.sum(ledger.cache_read_tokens), 0),
                func.coalesce(func.sum(ledger.cache_write_tokens), 0),
                func.coalesce(func.sum(ledger.cost_usd), 0),
            ).where(ledger.run_id == run_id, ledger.provider_kind == "llm")
        )
    ).one()
    if not calls:
        return usage
    return usage.model_copy(
        update={
            "llm_input_tokens": int(input_tokens),
            "llm_output_tokens": int(output_tokens),
            "llm_cache_read_tokens": int(cache_read),
            "llm_cache_write_tokens": int(cache_write),
            "llm_usd": round(float(cost), 6),
        }
    )


def _ms(later, earlier) -> int | None:
    if later is None or earlier is None:
        return None
    return int((later - earlier).total_seconds() * 1000)
