import asyncio
import uuid
from datetime import timedelta

import pytest
from arq.worker import Worker
from sqlalchemy import select, update

from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunStatus, RunTrigger
from app.models.runs import ActiveResultSet, ClientSchedule, PipelineRun, RunEvent, StageRun
from app.pipeline.contracts import StageResult
from app.pipeline.runs import RunCancellingConflict, RunInProgress, RunRequest
from app.pipeline.stages.finalize import FinalizeStage
from app.pipeline.stages.orchestration import SnapshotConfigStage
from app.pipeline.wiring import build_pipeline_services
from app.workers import jobs
from app.workers.queue import ArqJobQueue
from app.workers.reconciler import Reconciler
from tests.conftest import TEST_REDIS_URL
from tests.pipeline.fakes import FakeOut, Probe, catalog, fake_stage, pipeline
from tests.pipeline.support import (
    create_tenant,
    drop_workspace,
    fast_settings,
    make_container,
    run_pipeline,
    services,
)

pytestmark = pytest.mark.db


def _simple(probe: Probe):
    return catalog(
        [fake_stage("a", probe)],
        [pipeline(("a",)), pipeline(("a",), name="daily_refresh", kind=RunKind.daily_refresh)],
    )


async def test_same_kind_requests_join_the_active_run(container: Container, tenant: TenantScope) -> None:
    pipeline_services = services(container, _simple(Probe()))
    request = RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
    first, second = await asyncio.gather(
        pipeline_services.runs.request_run(tenant, request),
        pipeline_services.runs.request_run(tenant, request),
    )
    assert first.run.id == second.run.id
    assert sorted([first.created, second.created]) == [False, True]


async def test_daily_refresh_joins_analysis_and_analysis_supersedes_queued_daily(
    container: Container, tenant: TenantScope
) -> None:
    pipeline_services = services(container, _simple(Probe()))
    daily = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.daily_refresh, trigger=RunTrigger.user)
    )
    analysis = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
    )
    assert analysis.created and analysis.superseded_run_id == daily.run.id
    joined = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.daily_refresh, trigger=RunTrigger.schedule)
    )
    assert joined.run.id == analysis.run.id and joined.deduplicated
    async with container.session_factory() as session:
        old = await session.get(PipelineRun, daily.run.id)
    assert old.status == "superseded" and old.superseded_by_run_id == analysis.run.id


async def test_cancelling_run_blocks_a_new_request(container: Container, tenant: TenantScope) -> None:
    pipeline_services = services(container, _simple(Probe()))
    run = (
        await pipeline_services.runs.request_run(
            tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
        )
    ).run
    await pipeline_services.runs.claim(run.id, 1, "worker-a")
    await pipeline_services.runs.cancel(tenant, run.id)
    with pytest.raises(RunCancellingConflict) as conflict:
        await pipeline_services.runs.request_run(
            tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
        )
    assert conflict.value.retry_after_s == 5


async def test_unrelated_kind_conflicts_with_the_active_run(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()
    both = catalog(
        [fake_stage("a", probe)],
        [pipeline(("a",)), pipeline(("a",), name="onboarding", kind=RunKind.onboarding)],
    )
    pipeline_services = services(container, both)
    active = await pipeline_services.runs.request_run(
        tenant, RunRequest(kind=RunKind.onboarding, trigger=RunTrigger.user)
    )
    with pytest.raises(RunInProgress) as conflict:
        await pipeline_services.runs.request_run(
            tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
        )
    assert conflict.value.run_id == active.run.id


async def _cards(ctx, probe: Probe) -> StageResult[FakeOut]:
    return StageResult(status="succeeded", artifact=FakeOut(value=3))


async def test_finalize_publishes_atomically_and_books_schedules(
    container: Container, tenant: TenantScope
) -> None:
    probe = Probe()
    stages = [
        SnapshotConfigStage,
        fake_stage("write_cards", probe, deps=("snapshot_config",), behaviour=_cards),
        FinalizeStage,
    ]
    definition = pipeline(
        ("snapshot_config", "write_cards", "finalize"), name="daily_refresh", kind=RunKind.daily_refresh
    )
    pipeline_services = services(container, catalog(stages, [definition]))
    run_id, status = await run_pipeline(container, pipeline_services, tenant, kind=RunKind.daily_refresh)

    # no competitors or topics are confirmed, so the daily refresh carries a notice
    assert status is RunStatus.partial
    async with container.session_factory() as session:
        sets = (
            (
                await session.execute(
                    select(ActiveResultSet).where(ActiveResultSet.client_id == tenant.client_id)
                )
            )
            .scalars()
            .all()
        )
        schedule = (
            await session.execute(select(ClientSchedule).where(ClientSchedule.client_id == tenant.client_id))
        ).scalar_one()
        types = (
            (
                await session.execute(
                    select(RunEvent.type).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    assert sorted(row.window_days for row in sets) == [7, 30, 90]
    assert {row.run_id for row in sets} == {run_id}
    assert schedule.kind == "daily_refresh" and schedule.next_due_at is not None
    assert types[-2:] == ["results.published", "run.partial"]


async def test_analysis_without_confirmed_setup_fails_config_invalid(
    container: Container, tenant: TenantScope
) -> None:
    stages = [SnapshotConfigStage]
    pipeline_services = services(container, catalog(stages, [pipeline(("snapshot_config",))]))
    run_id, status = await run_pipeline(container, pipeline_services, tenant)
    assert status is RunStatus.failed
    async with container.session_factory() as session:
        run = await session.get(PipelineRun, run_id)
    assert run.error["code"] == "config_invalid"


async def test_reconciler_recovers_a_crashed_attempt(container: Container, tenant: TenantScope) -> None:
    probe = Probe()
    queue_names = [f"test:{uuid.uuid4().hex[:8]}:{name}" for name in ("i", "b", "m")]
    queue = ArqJobQueue.from_url(TEST_REDIS_URL, queue_names)
    try:
        pipeline_services = build_pipeline_services(container, job_queue=queue, catalog=_simple(probe))
        run = (
            await pipeline_services.runs.request_run(
                tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
            )
        ).run
        claimed = await pipeline_services.runs.claim(run.id, 1, "dead-worker")
        assert claimed is not None
        async with container.session_factory() as session, session.begin():
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run.id)
                .values(
                    lease_expires_at=claimed.lease_expires_at - timedelta(minutes=5), queue=queue_names[0]
                )
            )
            await session.execute(update(StageRun).where(StageRun.run_id == run.id).values(status="running"))
        reconciler = Reconciler(
            session_factory=container.session_factory,
            runs=pipeline_services.runs,
            queue=queue,
            maintenance_queue=queue_names[2],
        )
        report = await reconciler.run()
        assert report.recovered == 1
        async with container.session_factory() as session:
            recovered = await session.get(PipelineRun, run.id)
        assert recovered.attempt == 2 and recovered.lease_owner is None
        assert await queue.depth(queue_names[0]) == 1
    finally:
        await queue.redis.delete(*queue_names)
        await queue.aclose()


async def test_arq_worker_executes_an_enqueued_run(migrated_database: str) -> None:
    probe = Probe()
    prefix = f"test:{uuid.uuid4().hex[:8]}"
    queues = {
        "interactive_queue": f"{prefix}:i",
        "batch_queue": f"{prefix}:b",
        "maintenance_queue": f"{prefix}:m",
    }
    container = make_container(fast_settings(workers=queues))
    tenant = await create_tenant(container, "arqco")
    state = jobs.build_worker_state(container, catalog=_simple(probe))
    try:
        result = await state.services.runs.request_run(
            tenant, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
        )
        assert result.run.arq_job_id == f"run:{result.run.id}:1"
        # arq refuses a second job with the same id while the first is pending
        assert await state.queue.enqueue_run(result.run.id, 1, queues["interactive_queue"]) is None
        worker = Worker(
            functions=[jobs.run_pipeline],
            queue_name=queues["interactive_queue"],
            redis_pool=state.queue.redis,
            burst=True,
            ctx={"state": state},
            handle_signals=False,
            allow_abort_jobs=True,
            poll_delay=0.05,
        )
        await worker.main()
        async with container.session_factory() as session:
            run = await session.get(PipelineRun, result.run.id)
        assert run.status == "succeeded" and probe.calls["a"] == 1 and run.lease_owner is None
    finally:
        keys = [key async for key in state.queue.redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await state.queue.redis.delete(*keys)
        await drop_workspace(container, tenant.workspace_id)
        await container.aclose()
