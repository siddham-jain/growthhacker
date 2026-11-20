from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunStatus, RunTrigger
from app.models.runs import PipelineRun, RunEvent
from app.pipeline.config_source import config_hash
from app.pipeline.contracts import RunConfigSnapshot
from app.pipeline.executor import RunExecutor
from app.pipeline.plan import RunParams
from app.pipeline.runs import RunInProgress, RunRequest, confirm_decision
from app.pipeline.wiring import PipelineServices
from tests.pipeline.fakes import Probe, catalog, fake_stage, pipeline
from tests.pipeline.support import create_tenant, drop_workspace, services
from tests.refresh.fixtures import topic_ref

pytestmark = pytest.mark.db

FROZEN_COMPETITORS = [f"UC{index:022d}" for index in range(3)]


class FreezingConfigSource:
    """the table snapshot, plus the competitors and topic refs once the confirmer has frozen them."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.frozen = False

    async def snapshot(self, session, client, window_days) -> RunConfigSnapshot:
        snapshot = await self.inner.snapshot(session, client, window_days)
        if not self.frozen:
            return snapshot
        fields = {
            **snapshot.model_dump(mode="json"),
            "competitor_channel_ids": FROZEN_COMPETITORS,
            "topic_refs": [topic_ref("knee replacement").model_dump(mode="json")],
        }
        return RunConfigSnapshot.model_validate({**fields, "config_hash": config_hash(fields)})


class FakeConfirmer:
    def __init__(self, source: FreezingConfigSource, *, freezes: bool = True, error: Exception | None = None):
        self.source = source
        self.freezes = freezes
        self.error = error
        self.prepared = 0

    async def accept_suggestions(self, scope: TenantScope) -> None:
        raise AssertionError("manual confirm never accepts suggestions")

    async def prepare(self, scope: TenantScope) -> None:
        self.prepared += 1
        if self.error is not None:
            raise self.error
        self.source.frozen = self.freezes


@pytest.fixture
async def awaiting(container: Container) -> AsyncIterator[TenantScope]:
    scope = await create_tenant(container, "confirmco", lifecycle="awaiting_confirmation")
    yield scope
    await drop_workspace(container, scope.workspace_id)


def _wire(container: Container, probe: Probe, **confirmer: Any) -> tuple[PipelineServices, FakeConfirmer]:
    source = FreezingConfigSource(container.adapter("run_config_source"))
    fake = FakeConfirmer(source, **confirmer)
    container.override("run_config_source", source)
    container.override("setup_confirmer", fake)
    test_catalog = catalog(
        [fake_stage("a", probe)],
        [pipeline(("a",)), pipeline(("a",), name="daily_refresh", kind=RunKind.daily_refresh)],
    )
    return services(container, test_catalog), fake


async def _execute(container: Container, pipeline_services: PipelineServices, run: PipelineRun):
    executor = RunExecutor(
        container=container, runs=pipeline_services.runs, publisher=pipeline_services.publisher
    )
    return await executor.execute(run.id, run.attempt, "worker-a")


async def _row(container: Container, run_id) -> PipelineRun:
    async with container.session_factory() as session:
        return await session.get(PipelineRun, run_id)


async def test_confirm_queues_one_analysis_and_leaves_the_slow_work_to_the_worker(
    container: Container, awaiting: TenantScope
) -> None:
    probe = Probe()
    pipeline_services, fake = _wire(container, probe)
    first, counts = await pipeline_services.setup.confirm(awaiting, requested_by="user:1")
    second, _ = await pipeline_services.setup.confirm(awaiting, requested_by="user:1")

    assert first.created and first.run.status == RunStatus.queued.value
    assert RunParams.model_validate(first.run.params).confirm_setup
    assert fake.prepared == 0 and counts.competitors_confirmed == 0
    assert second.deduplicated and second.run.id == first.run.id
    async with container.session_factory() as session:
        active = await session.scalar(
            select(func.count()).where(
                PipelineRun.client_id == awaiting.client_id, PipelineRun.kind == RunKind.analysis.value
            )
        )
    assert active == 1


async def test_worker_freezes_the_setup_before_the_dag_runs(
    container: Container, awaiting: TenantScope
) -> None:
    probe = Probe()
    pipeline_services, fake = _wire(container, probe)
    result, _ = await pipeline_services.setup.confirm(awaiting)

    assert await _execute(container, pipeline_services, result.run) is RunStatus.succeeded
    run = await _row(container, result.run.id)
    assert fake.prepared == 1 and probe.calls["a"] == 1
    assert run.config_snapshot["competitor_channel_ids"] == FROZEN_COMPETITORS
    assert run.config_hash != result.run.config_hash
    assert RunParams.model_validate(run.params).setup_prepared


@pytest.mark.parametrize(
    ("confirmer", "code", "reason"),
    [
        ({"freezes": False}, "competitors_out_of_range", "competitors_out_of_range: 0 confirmed"),
        ({"error": RuntimeError("embedder offline")}, "setup_failed", "embedder offline"),
    ],
)
async def test_a_failed_background_confirm_fails_the_run_with_the_reason(
    container: Container, awaiting: TenantScope, confirmer: dict[str, Any], code: str, reason: str
) -> None:
    probe = Probe()
    pipeline_services, _ = _wire(container, probe, **confirmer)
    result, _ = await pipeline_services.setup.confirm(awaiting)

    assert await _execute(container, pipeline_services, result.run) is RunStatus.failed
    run = await _row(container, result.run.id)
    assert run.error["code"] == code and reason in run.error["message"]
    assert probe.calls["a"] == 0 and run.lease_owner is None
    async with container.session_factory() as session:
        events = (
            await session.execute(
                select(RunEvent.type, RunEvent.level, RunEvent.message)
                .where(RunEvent.run_id == run.id)
                .order_by(RunEvent.seq)
            )
        ).all()
    assert any(level == "error" and reason in message for _, level, message in events)
    assert events[-1].type == "run.failed"


async def test_confirm_supersedes_a_queued_refresh_and_refuses_to_join_a_running_one(
    container: Container, awaiting: TenantScope
) -> None:
    pipeline_services, _ = _wire(container, Probe())
    daily = await pipeline_services.runs.request_run(
        awaiting, RunRequest(kind=RunKind.daily_refresh, trigger=RunTrigger.user)
    )
    confirmed, _ = await pipeline_services.setup.confirm(awaiting)
    assert confirmed.superseded_run_id == daily.run.id

    await pipeline_services.runs.cancel(awaiting, confirmed.run.id)
    running = await pipeline_services.runs.request_run(
        awaiting, RunRequest(kind=RunKind.daily_refresh, trigger=RunTrigger.user)
    )
    assert await pipeline_services.runs.claim(running.run.id, 1, "worker-a") is not None
    with pytest.raises(RunInProgress):
        await pipeline_services.setup.confirm(awaiting)


def test_confirm_decision_never_skips_the_freeze() -> None:
    queued, running = RunStatus.queued, RunStatus.running
    assert (
        confirm_decision(active_kind=RunKind.analysis, active_status=running, active_confirms=True) == "join"
    )
    assert (
        confirm_decision(active_kind=RunKind.analysis, active_status=queued, active_confirms=False)
        == "supersede"
    )
    assert (
        confirm_decision(active_kind=RunKind.analysis, active_status=running, active_confirms=False)
        == "conflict"
    )
    assert (
        confirm_decision(active_kind=RunKind.recrawl, active_status=queued, active_confirms=False)
        == "conflict"
    )
    assert (
        confirm_decision(
            active_kind=RunKind.analysis, active_status=RunStatus.cancelling, active_confirms=True
        )
        == "cancelling"
    )
