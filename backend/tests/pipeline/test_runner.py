import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.runs import (
    RunEventType,
    RunKind,
    RunNotice,
    RunNoticeCode,
    RunStatus,
    RunTrigger,
    StageStatus,
)
from app.models.runs import PipelineRun, RunArtifact, RunEvent, StageRun
from app.pipeline.contracts import BudgetKind, RunContext, StageResult, TransientError
from app.pipeline.executor import RunExecutor
from app.pipeline.runs import RunRequest
from app.pipeline.wiring import PipelineServices
from tests.pipeline.fakes import FakeOut, Probe, catalog, fake_stage, pipeline, sum_upstream
from tests.pipeline.support import (
    create_tenant,
    drop_workspace,
    fast_settings,
    make_container,
    run_pipeline,
    services,
)

pytestmark = pytest.mark.db


async def _run(container: Container, run_id) -> PipelineRun:
    async with container.session_factory() as session:
        return await session.get(PipelineRun, run_id)


async def _stages(container: Container, run_id) -> dict[str, StageRun]:
    async with container.session_factory() as session:
        rows = (await session.execute(select(StageRun).where(StageRun.run_id == run_id))).scalars().all()
    return {row.stage: row for row in rows}


async def _events(container: Container, run_id) -> list[RunEvent]:
    async with container.session_factory() as session:
        return list(
            (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
            .scalars()
            .all()
        )


def _diamond(probe: Probe, **overrides: Any) -> list[type]:
    stages = {
        "a": fake_stage("a", probe),
        "b": fake_stage("b", probe, deps=("a",)),
        "c": fake_stage("c", probe, deps=("a",)),
        "d": fake_stage("d", probe, deps=("b", "c")),
    }
    stages.update(overrides)
    return list(stages.values())


def _services(container: Container, probe: Probe, **overrides: Any) -> PipelineServices:
    return services(container, catalog(_diamond(probe, **overrides), [pipeline(("a", "b", "c", "d"))]))


async def test_diamond_dag_runs_in_order_with_gap_free_events(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()
    run_id, status = await run_pipeline(container, _services(container, probe), tenant)

    assert status is RunStatus.succeeded
    stages = await _stages(container, run_id)
    assert {name: row.status for name, row in stages.items()} == dict.fromkeys("abcd", "succeeded")
    async with container.session_factory() as session:
        artifact = (
            await session.execute(
                select(RunArtifact).where(RunArtifact.run_id == run_id, RunArtifact.key == "fake_d")
            )
        ).scalar_one()
    assert FakeOut.model_validate(artifact.payload) == FakeOut(value=5, seen=["b", "c"])
    events = await _events(container, run_id)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[0].type == RunEventType.run_queued.value
    assert events[-1].type == RunEventType.run_succeeded.value
    assert sum(event.type == "stage.started" for event in events) == 4
    percents = [event.percent for event in events if event.percent is not None]
    assert percents == sorted(percents)
    run = await _run(container, run_id)
    assert run.progress_pct == 100 and run.lease_owner is None and run.event_seq == len(events)


async def test_independent_stages_run_in_parallel(container: Container, tenant: TenantScope) -> None:
    probe = Probe()

    async def slow(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        await asyncio.sleep(0.2)
        return StageResult(status="succeeded", artifact=await sum_upstream(ctx, ("a",), ()))

    pipeline_services = _services(
        container,
        probe,
        b=fake_stage("b", probe, deps=("a",), behaviour=slow),
        c=fake_stage("c", probe, deps=("a",), behaviour=slow),
    )
    _, status = await run_pipeline(container, pipeline_services, tenant)
    assert status is RunStatus.succeeded
    assert probe.max_running == 2


async def test_noncritical_failure_degrades_to_partial(container: Container, tenant: TenantScope) -> None:
    probe = Probe()

    async def boom(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        raise ValueError("source unavailable")

    stages = [
        fake_stage("a", probe),
        fake_stage("c", probe, deps=("a",), behaviour=boom, critical=False),
        fake_stage("needs_c", probe, deps=("c",)),
        fake_stage("maybe_c", probe, deps=("a",), optional=("c",)),
    ]
    pipeline_services = services(container, catalog(stages, [pipeline(("a", "c", "needs_c", "maybe_c"))]))
    run_id, status = await run_pipeline(container, pipeline_services, tenant)

    assert status is RunStatus.partial
    rows = await _stages(container, run_id)
    assert rows["c"].status == "failed" and rows["c"].error["code"] == "stage_failed"
    assert rows["needs_c"].status == "skipped"
    assert rows["maybe_c"].status == "succeeded"
    assert probe.calls["needs_c"] == 0


async def test_critical_failure_fails_run_after_siblings_finish(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()

    async def boom(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        raise RuntimeError("broken")

    async def slow(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        await asyncio.sleep(0.2)
        return StageResult(status="succeeded", artifact=FakeOut(value=1))

    pipeline_services = _services(
        container,
        probe,
        b=fake_stage("b", probe, deps=("a",), behaviour=boom),
        c=fake_stage("c", probe, deps=("a",), behaviour=slow),
    )
    run_id, status = await run_pipeline(container, pipeline_services, tenant)

    assert status is RunStatus.failed
    run = await _run(container, run_id)
    assert run.error["stage"] == "b" and run.error["code"] == "stage_failed"
    rows = await _stages(container, run_id)
    assert rows["c"].status == "succeeded"
    assert rows["d"].status == "skipped" and probe.calls["d"] == 0


async def test_transient_error_is_retried_in_process(container: Container, tenant: TenantScope) -> None:
    probe = Probe()

    async def flaky(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        if probe.calls["a"] == 1:
            raise TransientError("503 from upstream", retry_after_s=0)
        return StageResult(status="succeeded", artifact=FakeOut(value=1))

    pipeline_services = _services(container, probe, a=fake_stage("a", probe, behaviour=flaky))
    run_id, status = await run_pipeline(container, pipeline_services, tenant)

    assert status is RunStatus.succeeded
    assert (await _stages(container, run_id))["a"].attempt == 2
    assert any(event.type == "stage.retrying" for event in await _events(container, run_id))


async def test_resume_reuses_completed_stages(container: Container, tenant: TenantScope) -> None:
    probe = Probe()
    broken = {"value": True}

    async def sometimes(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        if broken["value"]:
            raise RuntimeError("writer crashed")
        return StageResult(status="succeeded", artifact=await sum_upstream(ctx, ("a",), ()))

    pipeline_services = _services(
        container, probe, c=fake_stage("c", probe, deps=("a",), behaviour=sometimes)
    )
    first_id, status = await run_pipeline(container, pipeline_services, tenant)
    assert status is RunStatus.failed

    broken["value"] = False
    retried = await pipeline_services.runs.retry(tenant, first_id)
    executor = RunExecutor(
        container=container, runs=pipeline_services.runs, publisher=pipeline_services.publisher
    )
    status = await executor.execute(retried.run.id, 1, "worker-b")

    assert status is RunStatus.succeeded
    assert retried.run.resume_of_run_id == first_id
    rows = await _stages(container, retried.run.id)
    assert rows["a"].status == "reused" and rows["a"].reused_from_run_id == first_id
    assert rows["b"].status == "reused"
    assert rows["c"].status == "succeeded" and rows["d"].status == "succeeded"
    assert probe.calls["a"] == 1 and probe.calls["b"] == 1 and probe.calls["c"] == 2


async def test_force_skips_cross_run_reuse(container: Container, tenant: TenantScope) -> None:
    probe = Probe()
    pipeline_services = _services(container, probe)
    await run_pipeline(container, pipeline_services, tenant)
    _, status = await run_pipeline(container, pipeline_services, tenant, force=True)
    assert status is RunStatus.succeeded
    assert probe.calls["a"] == 2

    _, status = await run_pipeline(container, pipeline_services, tenant)
    assert probe.calls["a"] == 2


async def test_cancel_reaches_a_running_stage_at_its_checkpoint(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()

    async def long_loop(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        probe.gate("b_started").set()
        for _ in range(200):
            await ctx.checkpoint()
            await asyncio.sleep(0.02)
        return StageResult(status="succeeded", artifact=FakeOut(value=1))

    pipeline_services = _services(
        container, probe, b=fake_stage("b", probe, deps=("a",), behaviour=long_loop)
    )
    result = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
    )
    executor = RunExecutor(
        container=container, runs=pipeline_services.runs, publisher=pipeline_services.publisher
    )
    task = asyncio.create_task(executor.execute(result.run.id, 1, "worker-a"))
    await asyncio.wait_for(probe.gate("b_started").wait(), timeout=5)
    cancelled = await pipeline_services.runs.cancel(tenant, result.run.id, "user")
    assert cancelled.status == "cancelling"

    assert await asyncio.wait_for(task, timeout=10) is RunStatus.cancelled
    rows = await _stages(container, result.run.id)
    assert rows["b"].status == "cancelled"
    events = await _events(container, result.run.id)
    assert events[-1].type == RunEventType.run_cancelled.value


async def test_shutdown_interrupts_then_next_attempt_resumes(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()

    async def hang(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        if probe.calls["b"] == 1:
            probe.gate("b_started").set()
            await asyncio.sleep(60)
        return StageResult(status="succeeded", artifact=await sum_upstream(ctx, ("a",), ()))

    pipeline_services = _services(container, probe, b=fake_stage("b", probe, deps=("a",), behaviour=hang))
    result = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
    )
    executor = RunExecutor(
        container=container, runs=pipeline_services.runs, publisher=pipeline_services.publisher
    )
    task = asyncio.create_task(executor.execute(result.run.id, 1, "worker-a"))
    await asyncio.wait_for(probe.gate("b_started").wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await _run(container, result.run.id)
    assert run.status == "running"
    rows = await _stages(container, result.run.id)
    assert rows["b"].status == "interrupted"

    status = await executor.execute(result.run.id, 2, "worker-b")
    assert status is RunStatus.succeeded
    assert probe.calls["a"] == 1
    assert (await _run(container, result.run.id)).attempt == 2


async def test_second_executor_cannot_steal_a_live_lease(container: Container, tenant: TenantScope) -> None:
    pipeline_services = _services(container, Probe())
    result = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
    )
    assert await pipeline_services.runs.claim(result.run.id, 1, "worker-a") is not None
    assert await pipeline_services.runs.claim(result.run.id, 1, "worker-b") is None


async def test_budget_exceeded_fails_the_stage(container: Container, tenant: TenantScope) -> None:
    probe = Probe()

    async def spender(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        ctx.budget.charge(BudgetKind.youtube_units, 40)
        ctx.budget.charge(BudgetKind.youtube_units, 40)
        return StageResult(status="succeeded", artifact=FakeOut(value=1))

    pipeline_services = _services(
        container, probe, a=fake_stage("a", probe, behaviour=spender, budget={"youtube_units": 50})
    )
    run_id, status = await run_pipeline(container, pipeline_services, tenant)
    assert status is RunStatus.failed
    rows = await _stages(container, run_id)
    assert rows["a"].error["code"] == "budget_exceeded"
    assert rows["a"].usage["youtube_units"] == 40


async def test_stage_notices_make_the_run_partial(container: Container, tenant: TenantScope) -> None:
    probe = Probe()

    async def noisy(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
        await ctx.reporter.notice(RunNotice(code=RunNoticeCode.quota_limited, message="Quota reached"))
        return StageResult(status="degraded", artifact=FakeOut(value=1))

    pipeline_services = _services(container, probe, a=fake_stage("a", probe, behaviour=noisy))
    run_id, status = await run_pipeline(container, pipeline_services, tenant)
    assert status is RunStatus.partial
    run = await _run(container, run_id)
    assert run.notices[0]["code"] == "quota_limited" and run.notices[0]["stage"] == "a"
    async with container.session_factory() as session:
        stored = (
            await session.execute(
                select(RunArtifact).where(RunArtifact.run_id == run_id, RunArtifact.key == "fake_a")
            )
        ).scalar_one()
    # degraded output feeds this run but never matches a later reuse lookup
    assert stored.fingerprint.endswith(":degraded")


async def test_missing_inherited_input_fails_cleanly(container: Container, tenant: TenantScope) -> None:
    probe = Probe()
    stages = [fake_stage("upstream", probe), fake_stage("consumer", probe, deps=("upstream",))]
    definition = pipeline(("consumer",), name="cards", kind=RunKind.cards, inherited=("upstream",))
    pipeline_services = services(container, catalog(stages, [definition]))
    run_id, status = await run_pipeline(container, pipeline_services, tenant, kind=RunKind.cards)
    assert status is RunStatus.failed
    assert (await _run(container, run_id)).error["code"] == "prerequisite_missing"


async def test_inherited_artifacts_are_copied_from_the_latest_run(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()
    stages = [fake_stage("upstream", probe), fake_stage("consumer", probe, deps=("upstream",))]
    full = pipeline(("upstream", "consumer"))
    lazy = pipeline(("consumer",), name="cards", kind=RunKind.cards, inherited=("upstream",))
    pipeline_services = services(container, catalog(stages, [full, lazy]))
    first_id, _ = await run_pipeline(container, pipeline_services, tenant)
    run_id, status = await run_pipeline(container, pipeline_services, tenant, kind=RunKind.cards, force=True)
    assert status is RunStatus.succeeded
    rows = await _stages(container, run_id)
    assert rows["upstream"].status == "reused" and rows["upstream"].reused_from_run_id == first_id
    assert probe.calls["upstream"] == 1 and probe.calls["consumer"] == 2


async def test_wall_budget_stops_the_run(migrated_database: str) -> None:
    settings = fast_settings(
        pipeline={
            "budgets": {
                "analysis": {
                    "wall_s": 0.3,
                    "youtube_units": 10,
                    "search_calls": 1,
                    "llm_usd": 1,
                    "tavily_credits": 1,
                }
            }
        }
    )
    container = make_container(settings)
    scope = await create_tenant(container, "slowco")
    try:
        probe = Probe()

        async def slow(ctx: RunContext[Any], probe: Probe) -> StageResult[FakeOut]:
            await asyncio.sleep(5)
            return StageResult(status="succeeded", artifact=FakeOut(value=1))

        pipeline_services = _services(container, probe, a=fake_stage("a", probe, behaviour=slow))
        run_id, status = await run_pipeline(container, pipeline_services, scope)
        assert status is RunStatus.failed
        assert (await _run(container, run_id)).error["code"] == "timeout"
        assert (await _stages(container, run_id))["a"].status == StageStatus.cancelled.value
    finally:
        await drop_workspace(container, scope.workspace_id)
        await container.aclose()


async def test_dependents_start_only_after_the_artifact_is_committed(
    container: Container, tenant: TenantScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.pipeline.checkpoints import CheckpointStore

    original = CheckpointStore.record

    async def slow_record(self, run_id, record, *, artifact=None, copy_from=None):
        # a large artifact takes a while to write; the scheduler keeps polling meanwhile
        if artifact is not None and artifact[0].name == "fake_a":
            await asyncio.sleep(0.5)
        await original(self, run_id, record, artifact=artifact, copy_from=copy_from)

    monkeypatch.setattr(CheckpointStore, "record", slow_record)
    probe = Probe()
    run_id, status = await run_pipeline(container, _services(container, probe), tenant)
    assert status is RunStatus.succeeded
    stages = await _stages(container, run_id)
    assert {name: row.status for name, row in stages.items()} == dict.fromkeys("abcd", "succeeded")
