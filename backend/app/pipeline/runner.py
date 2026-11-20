import asyncio
import hashlib
import re
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.clock import Clock
from app.core.config import PipelineSettings
from app.domain.runs import RunEventType, RunNotice, RunStatus, StageStatus
from app.pipeline import progress_messages as messages
from app.pipeline.artifacts import ArtifactTooLarge, PgArtifactStore, RunArtifactReader
from app.pipeline.checkpoints import CheckpointStore, StageRecord
from app.pipeline.context import RunBudgetLedger, RunCancellation
from app.pipeline.contracts import (
    BudgetExceeded,
    RunCancelled,
    RunContext,
    StageResult,
    TransientError,
    UsageLedger,
)
from app.pipeline.fingerprint import as_of_bucket, degraded, stage_fingerprint
from app.pipeline.lifecycle import STAGE_PRODUCED, STAGE_TERMINAL, StageStateMachine
from app.pipeline.plan import RunError, RunOutcome, RunPlan, RunTimings, StageOutcomeView
from app.pipeline.progress import STAGE_TERMINAL_EVENT, ProgressTracker, RunProgress
from app.pipeline.registry import PipelineCatalog, PipelineDefinition

SKIP_DISABLED = "disabled"
SKIP_NOT_APPLICABLE = "not_applicable"
SKIP_UPSTREAM_FAILED = "upstream_failed"


class PrerequisiteMissing(LookupError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"no earlier '{stage}' result exists for this client")
        self.stage = stage


class _WallTimeout(Exception):
    pass


@dataclass
class _StageState:
    name: str
    stage: Any
    status: StageStatus = StageStatus.pending
    skip_reason: str | None = None
    fingerprint: str | None = None
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: RunError | None = None
    usage: UsageLedger = field(default_factory=UsageLedger)

    def move(self, target: StageStatus) -> None:
        StageStateMachine.ensure(self.status, target)
        self.status = target

    @property
    def satisfies_requirement(self) -> bool:
        if self.status in STAGE_PRODUCED:
            return True
        return self.status is StageStatus.skipped and self.skip_reason != SKIP_UPSTREAM_FAILED

    @property
    def terminal(self) -> bool:
        return self.status in STAGE_TERMINAL


def tracker_for(definition: PipelineDefinition, catalog: PipelineCatalog) -> ProgressTracker:
    return ProgressTracker(
        {
            name: (catalog.stage(name).weight, catalog.stage(name).policy.timeout_s / 4)
            for name in definition.stages
        }
    )


def error_from_exception(error: BaseException, stage: str, code: str = "stage_failed") -> RunError:
    frames = traceback.extract_tb(error.__traceback__)
    top = f"{frames[-1].filename}:{frames[-1].name}" if frames else "?"
    template = re.sub(r"\d+", "N", str(error))[:200]
    fingerprint = hashlib.sha1(f"{type(error).__name__}|{top}|{template}".encode()).hexdigest()[:16]
    retryable = isinstance(error, TransientError | TimeoutError)
    message = f"{type(error).__name__}: {error}"[:500]
    declared = getattr(error, "code", None)
    if isinstance(declared, str):
        code = declared
    return RunError(code=code, stage=stage, message=message, retryable=retryable, fingerprint=fingerprint)


class DagRunner:
    """generic dag executor; it knows stages only through the catalog and their declared deps."""

    def __init__(
        self,
        *,
        catalog: PipelineCatalog,
        artifacts: PgArtifactStore,
        checkpoints: CheckpointStore,
        settings: PipelineSettings,
        quota_tz: ZoneInfo,
        clock: Clock,
        business_clock: Clock,
        deps: Any,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._catalog = catalog
        self._artifacts = artifacts
        self._checkpoints = checkpoints
        self._settings = settings
        self._quota_tz = quota_tz
        self._clock = clock
        self._business_clock = business_clock
        self._deps = deps
        self._sleep = sleep

    async def execute(
        self, plan: RunPlan, progress: RunProgress, cancel: RunCancellation, ledger: RunBudgetLedger
    ) -> RunOutcome:
        started = self._clock.now()
        definition = plan.definition
        states = {name: _StageState(name, self._catalog.stage(name)) for name in definition.stages}
        error: RunError | None = None
        timed_out = False
        try:
            inherited = await self._inherit(plan, states, progress)
            await self._skip_disabled(plan, states, progress)
            error = await self._schedule(plan, states, inherited, progress, cancel, ledger)
        except PrerequisiteMissing as missing:
            error = RunError(code="prerequisite_missing", stage=missing.stage, message=str(missing))
        except _WallTimeout:
            timed_out = True
            error = RunError(code="timeout", message="run exceeded its wall-time budget", retryable=True)
        status = self._final_status(states, progress.notices, cancel, error, timed_out)
        return RunOutcome(
            status=status,
            error=error if status is RunStatus.failed else None,
            notices=list(progress.notices),
            usage=ledger.usage(),
            stages={
                name: StageOutcomeView(
                    status=state.status,
                    started_at=state.started_at,
                    finished_at=state.finished_at,
                    error=state.error,
                )
                for name, state in states.items()
            },
            timings=RunTimings(run_ms=int((self._clock.now() - started).total_seconds() * 1000)),
        )

    def _final_status(
        self,
        states: dict[str, _StageState],
        notices: list[RunNotice],
        cancel: RunCancellation,
        error: RunError | None,
        timed_out: bool,
    ) -> RunStatus:
        if cancel.is_cancelled():
            return RunStatus.cancelled
        if timed_out or error is not None:
            return RunStatus.failed
        gaps = any(
            state.status in {StageStatus.degraded, StageStatus.failed}
            or (state.status is StageStatus.skipped and state.skip_reason == SKIP_UPSTREAM_FAILED)
            for state in states.values()
        )
        return RunStatus.partial if gaps or notices else RunStatus.succeeded

    async def _inherit(
        self, plan: RunPlan, states: dict[str, _StageState], progress: RunProgress
    ) -> dict[str, str]:
        definition = plan.definition
        required = {definition.resolve(dep) for state in states.values() for dep in state.stage.depends_on}
        optional = {definition.resolve(dep) for state in states.values() for dep in state.stage.optional_deps}
        fingerprints: dict[str, str] = {}
        for name in definition.inherited:
            if name not in required | optional:
                continue
            stage = self._catalog.stage(name)
            latest = await self._artifacts.find_latest(stage.produces, exclude_run_id=plan.run_id)
            if latest is None:
                if name in required:
                    raise PrerequisiteMissing(name)
                continue
            now = self._clock.now()
            await self._checkpoints.record(
                plan.run_id,
                StageRecord(
                    stage=name,
                    stage_version=stage.version,
                    status=StageStatus.reused.value,
                    attempt=0,
                    input_fingerprint=latest.fingerprint,
                    reused_from_run_id=latest.run_id,
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                ),
                copy_from=(latest.run_id, stage.produces.name),
            )
            fingerprints[name] = latest.fingerprint
            await progress.emit(
                RunEventType.stage_reused,
                messages.stage_reused(name),
                stage=name,
                status=StageStatus.reused,
            )
        return fingerprints

    async def _skip_disabled(
        self, plan: RunPlan, states: dict[str, _StageState], progress: RunProgress
    ) -> None:
        records = []
        for name, state in states.items():
            if name in self._settings.disabled_stages or not state.stage.enabled(plan.snapshot):
                state.move(StageStatus.skipped)
                state.skip_reason = SKIP_DISABLED
                progress.tracker.finish(name)
                records.append(self._record(state, plan))
        await self._checkpoints.record_many(plan.run_id, records)
        for record in records:
            await progress.emit(
                RunEventType.stage_skipped,
                messages.stage_finished(record.stage, "skipped"),
                stage=record.stage,
                status=StageStatus.skipped,
            )

    async def _schedule(
        self,
        plan: RunPlan,
        states: dict[str, _StageState],
        inherited: dict[str, str],
        progress: RunProgress,
        cancel: RunCancellation,
        ledger: RunBudgetLedger,
    ) -> RunError | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + plan.budget.wall_s
        tasks: dict[asyncio.Task[None], str] = {}
        failure: RunError | None = None
        no_reuse = self._recompute_set(plan)
        try:
            while True:
                ready, blocked = self._ready(plan.definition, states, inherited)
                for name in blocked:
                    await self._skip_blocked(plan, states[name], progress)
                if blocked:
                    continue
                if failure is None and not cancel.is_cancelled():
                    in_flight = set(tasks.values())
                    queued = [name for name in ready if name not in in_flight]
                    for name in queued[: self._settings.max_parallel_stages - len(tasks)]:
                        task = asyncio.create_task(
                            self._run_stage(plan, states, inherited, progress, cancel, ledger, name, no_reuse)
                        )
                        tasks[task] = name
                if not tasks:
                    return failure
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise _WallTimeout
                done, _ = await asyncio.wait(
                    tasks,
                    timeout=min(remaining, self._settings.cancel_poll_s),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel.is_cancelled():
                    raise RunCancelled(cancel.reason or "user")
                for task in done:
                    name = tasks.pop(task)
                    exception = task.exception()
                    if isinstance(exception, RunCancelled):
                        cancel.request(str(exception) or "user")
                        raise exception
                    if exception is not None:
                        raise exception
                    state = states[name]
                    if state.status is StageStatus.failed and state.stage.policy.critical and failure is None:
                        failure = state.error
        except (RunCancelled, _WallTimeout):
            await self._stop(plan, states, tasks, progress, StageStatus.cancelled)
            if cancel.is_cancelled():
                return None
            raise
        except asyncio.CancelledError:
            # a hard abort with a cancel request is a cancellation; otherwise a shutdown interruption
            target = StageStatus.cancelled if cancel.is_cancelled() else StageStatus.interrupted
            await asyncio.shield(self._stop(plan, states, tasks, progress, target))
            raise

    def _ready(
        self, definition: PipelineDefinition, states: dict[str, _StageState], inherited: dict[str, str]
    ) -> tuple[list[str], list[str]]:
        ready: list[str] = []
        blocked: list[str] = []
        for name in definition.stages:
            state = states[name]
            if state.status is not StageStatus.pending:
                continue
            waiting = False
            for dependency in state.stage.depends_on:
                resolved = definition.resolve(dependency)
                if resolved in inherited:
                    continue
                upstream = states[resolved]
                if upstream.satisfies_requirement:
                    continue
                if upstream.terminal:
                    blocked.append(name)
                waiting = True
                break
            if waiting:
                continue
            optional_pending = any(
                not states[resolved].terminal
                for dependency in state.stage.optional_deps
                if (resolved := definition.resolve(dependency)) in states
            )
            if not optional_pending:
                ready.append(name)
        return ready, blocked

    async def _skip_blocked(self, plan: RunPlan, state: _StageState, progress: RunProgress) -> None:
        state.move(StageStatus.skipped)
        state.skip_reason = SKIP_UPSTREAM_FAILED
        progress.tracker.finish(state.name)
        await self._checkpoints.record(plan.run_id, self._record(state, plan))
        await progress.emit(
            RunEventType.stage_skipped,
            messages.stage_finished(state.name, "skipped"),
            stage=state.name,
            status=StageStatus.skipped,
            level="warning",
        )

    async def _stop(
        self,
        plan: RunPlan,
        states: dict[str, _StageState],
        tasks: dict[asyncio.Task[None], str],
        progress: RunProgress,
        target: StageStatus,
    ) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        now = self._clock.now()
        records = []
        for state in states.values():
            if state.status in {StageStatus.running, StageStatus.pending} and (
                state.status is StageStatus.running or target is StageStatus.cancelled
            ):
                state.move(target)
                state.finished_at = now
                records.append(self._record(state, plan))
        await self._checkpoints.record_many(plan.run_id, records)
        for record in records:
            await progress.emit(
                RunEventType.stage_retrying
                if target is StageStatus.interrupted
                else RunEventType.stage_skipped,
                messages.stage_finished(record.stage, target.value),
                stage=record.stage,
                status=target,
                level="warning",
            )

    def _recompute_set(self, plan: RunPlan) -> frozenset[str]:
        # from_stage and everything downstream of it is never reused from another run
        start = plan.params.from_stage
        if start is None or start not in plan.definition.stages:
            return frozenset()
        selected = {start}
        changed = True
        while changed:
            changed = False
            for name in plan.definition.stages:
                stage = self._catalog.stage(name)
                deps = {plan.definition.resolve(dep) for dep in (*stage.depends_on, *stage.optional_deps)}
                if name not in selected and deps & selected:
                    selected.add(name)
                    changed = True
        return frozenset(selected)

    def _fingerprint(
        self,
        plan: RunPlan,
        state: _StageState,
        states: dict[str, _StageState],
        inherited: dict[str, str],
        ctx,
    ) -> str:
        stage = state.stage
        upstream: dict[str, str] = {}
        for dependency in (*stage.depends_on, *stage.optional_deps):
            resolved = plan.definition.resolve(dependency)
            if resolved in inherited:
                upstream[dependency] = inherited[resolved]
            elif (
                resolved in states
                and states[resolved].fingerprint
                and states[resolved].status in STAGE_PRODUCED
            ):
                upstream[dependency] = states[resolved].fingerprint
        return stage_fingerprint(
            name=state.name,
            version=stage.version,
            schema_version=stage.produces.schema_version,
            upstream=upstream,
            inputs=stage.fingerprint_inputs(ctx),
            bucket=as_of_bucket(
                plan.as_of, self._settings.fingerprint_bucket_h.get(state.name), self._quota_tz
            ),
        )

    async def _try_reuse(
        self, plan: RunPlan, state: _StageState, progress: RunProgress, no_reuse: frozenset[str]
    ) -> bool:
        stage = state.stage
        assert state.fingerprint is not None
        same_run = await self._artifacts.fingerprint_of(plan.run_id, stage.produces)
        if same_run == state.fingerprint:
            state.move(StageStatus.reused)
            await progress.emit(
                RunEventType.stage_reused,
                messages.stage_resuming(state.name)
                if plan.attempt > 1
                else messages.stage_reused(state.name),
                stage=state.name,
                status=StageStatus.reused,
            )
            return True
        if not stage.policy.reusable or plan.params.force or state.name in no_reuse:
            return False
        max_age_h = self._settings.reuse_max_age_overrides_h.get(state.name, self._settings.reuse_max_age_h)
        found = await self._artifacts.find_reusable(
            self._artifacts.client_id, stage.produces, state.fingerprint, timedelta(hours=max_age_h)
        )
        if found is None or found[0] == plan.run_id:
            return False
        source_run_id, _ = found
        state.move(StageStatus.reused)
        now = self._clock.now()
        state.started_at = state.finished_at = now
        await self._checkpoints.record(
            plan.run_id,
            replace(self._record(state, plan), reused_from_run_id=source_run_id, duration_ms=0),
            copy_from=(source_run_id, stage.produces.name),
        )
        await progress.emit(
            RunEventType.stage_reused,
            messages.stage_reused(state.name),
            stage=state.name,
            status=StageStatus.reused,
        )
        return True

    async def _run_stage(
        self,
        plan: RunPlan,
        states: dict[str, _StageState],
        inherited: dict[str, str],
        progress: RunProgress,
        cancel: RunCancellation,
        ledger: RunBudgetLedger,
        name: str,
        no_reuse: frozenset[str],
    ) -> None:
        state = states[name]
        stage = state.stage
        reporter = progress.for_stage(name)
        stage_ledger = ledger.for_stage(stage.policy.budget)
        ctx = RunContext(
            run_id=plan.run_id,
            kind=plan.kind,
            trigger=plan.trigger,
            attempt=plan.attempt,
            as_of=plan.as_of,
            snapshot=plan.snapshot,
            clock=self._business_clock,
            budget=stage_ledger,
            reporter=reporter,
            artifacts=RunArtifactReader(self._artifacts, plan.run_id),
            cancel=cancel,
            deps=self._deps,
            stage=name,
        )
        state.fingerprint = self._fingerprint(plan, state, states, inherited, ctx)
        if await self._try_reuse(plan, state, progress, no_reuse):
            progress.tracker.finish(name)
            return
        state.move(StageStatus.running)
        state.started_at = self._clock.now()
        progress.tracker.start(name)
        await self._checkpoints.record(plan.run_id, self._record(state, plan))
        await progress.emit(
            RunEventType.stage_started, messages.stage_started(name), stage=name, status=StageStatus.running
        )
        result, error = await self._attempts(plan, state, ctx, reporter, progress)
        state.usage = stage_ledger.usage()
        artifact = None
        if error is None:
            assert result is not None
            artifact, error = self._accept(state, result)
        await self._finish(plan, state, progress, result, artifact, error)

    async def _attempts(self, plan: RunPlan, state: _StageState, ctx, reporter, progress: RunProgress):
        policy = state.stage.policy
        loop = asyncio.get_running_loop()
        for attempt in range(1, policy.max_attempts + 1):
            state.attempt = attempt
            soft = None if policy.soft_timeout_s is None else loop.time() + policy.soft_timeout_s
            stage_ctx = ctx.for_stage(state.name, reporter, soft_deadline=soft)
            try:
                async with asyncio.timeout(policy.timeout_s):
                    return await state.stage.run(stage_ctx), None
            except RunCancelled:
                raise
            except BudgetExceeded as error:
                return None, error_from_exception(error, state.name, code="budget_exceeded")
            except Exception as error:
                if isinstance(error, policy.retry_on) and attempt < policy.max_attempts:
                    delay = getattr(error, "retry_after_s", None)
                    if delay is None:
                        delay = (
                            policy.backoff_s[min(attempt - 1, len(policy.backoff_s) - 1)]
                            if policy.backoff_s
                            else 0
                        )
                    await progress.emit(
                        RunEventType.stage_retrying,
                        messages.stage_retrying(state.name, attempt + 1),
                        stage=state.name,
                        status=StageStatus.running,
                        level="warning",
                    )
                    await self._sleep(delay)
                    continue
                code = "timeout" if isinstance(error, TimeoutError) else "stage_failed"
                return None, error_from_exception(error, state.name, code=code)
        raise AssertionError("unreachable: max_attempts >= 1")

    def _accept(self, state: _StageState, result: Any):
        key = state.stage.produces
        if not isinstance(result, StageResult) or result.status not in {"succeeded", "degraded", "skipped"}:
            return None, RunError(
                code="invalid_result", stage=state.name, message="stage returned no StageResult"
            )
        if result.artifact is None:
            return None, None
        if not isinstance(result.artifact, key.model):
            message = f"stage produced {type(result.artifact).__name__}, expected {key.model.__name__}"
            return None, RunError(code="invalid_result", stage=state.name, message=message)
        fingerprint = state.fingerprint if result.status == "succeeded" else degraded(state.fingerprint or "")
        return (key, result.artifact, fingerprint), None

    async def _finish(
        self,
        plan: RunPlan,
        state: _StageState,
        progress: RunProgress,
        result: StageResult[Any] | None,
        artifact,
        error: RunError | None,
    ) -> None:
        counts: dict[str, int] = {} if result is None else dict(result.counts)
        if suppressed := progress.suppressed_insights(state.name):
            counts["insights_suppressed"] = suppressed
        if error is None and result is not None:
            for notice in result.notices:
                await progress.notice(
                    notice if notice.stage else notice.model_copy(update={"stage": state.name})
                )
        state.finished_at = self._clock.now()
        target = StageStatus.failed if error is not None else StageStatus(result.status)  # type: ignore[union-attr]
        if target is StageStatus.skipped:
            state.skip_reason = SKIP_NOT_APPLICABLE
        state.error = error
        StageStateMachine.ensure(state.status, target)
        # the scheduler reads in-memory states, so dependents may only see the result once the artifact is committed
        try:
            await self._checkpoints.record(
                plan.run_id, self._record(state, plan, counts, status=target), artifact=artifact
            )
        except ArtifactTooLarge as too_large:
            target = StageStatus.failed
            state.error = error_from_exception(too_large, state.name, code="artifact_too_large")
            await self._checkpoints.record(plan.run_id, self._record(state, plan, counts, status=target))
        state.status = target
        progress.tracker.finish(state.name)
        await progress.emit(
            STAGE_TERMINAL_EVENT[state.status],
            messages.stage_finished(state.name, state.status.value),
            stage=state.name,
            status=state.status,
            level="error" if state.status is StageStatus.failed else "info",
        )

    def _record(
        self,
        state: _StageState,
        plan: RunPlan,
        counts: dict[str, int] | None = None,
        status: StageStatus | None = None,
    ) -> StageRecord:
        duration = None
        if state.started_at is not None and state.finished_at is not None:
            duration = int((state.finished_at - state.started_at).total_seconds() * 1000)
        return StageRecord(
            stage=state.name,
            stage_version=state.stage.version,
            status=(status or state.status).value,
            attempt=state.attempt,
            input_fingerprint=state.fingerprint,
            started_at=state.started_at,
            finished_at=state.finished_at,
            duration_ms=duration,
            counts=counts,
            usage=state.usage.model_dump(mode="json"),
            error=None if state.error is None else state.error.model_dump(mode="json"),
            warnings=[{"skip_reason": state.skip_reason}] if state.skip_reason else [],
        )
