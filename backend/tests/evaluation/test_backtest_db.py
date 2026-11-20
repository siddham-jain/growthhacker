import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.api.errors import NotFound
from app.core.clock import FrozenClock
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunTrigger
from app.evaluation.api import BacktestApi
from app.evaluation.backtest.runner import BacktestRunner, OutcomesNotMature, eval_id_for
from app.evaluation.backtest.store import BacktestStore, ConfigChanged
from app.evaluation.freeze.bundle import BundleStore
from app.evaluation.freeze.job import FreezeJob, FreezeRefused
from app.evaluation.freeze.purge import purge_expired_bundles
from app.evaluation.freeze.store import FreezeRepository
from app.models.evaluation import EvalConfig, EvalFreeze, EvalOutcomeVideo, EvalPatternSpec, EvalResult
from app.models.runs import PipelineRun
from app.pipeline.contracts import RunContext
from app.pipeline.stages.evaluation import FROZEN_CORPUS, FrozenCorpusStage, RecordBacktestStage
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from tests.analytics.test_golden import build_inputs
from tests.analytics.test_stage import Ledger, NoCancel, Reporter, StaticSource
from tests.conftest import make_settings
from tests.evaluation.replay_fixtures import FREEZE_AT, snapshot
from tests.evaluation.test_backtest_replay import idea, output_with
from tests.foundation.factories import create_tenant, insert_row

pytestmark = pytest.mark.db


class FixedConfigSource:
    def __init__(self, snapshot) -> None:
        self.value = snapshot

    async def snapshot(self, session, client, window_days):
        return self.value


@dataclass
class Container:
    settings: Any
    session_factory: Any
    clock: Any
    adapters: dict[str, Any] = field(default_factory=dict)

    def adapter(self, name: str) -> Any:
        return self.adapters[name]


class Artifacts:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get(self, key):
        return self.values[key.name]

    async def get_optional(self, key):
        return self.values.get(key.name)


async def tenant(session_factory) -> TenantScope:
    async with session_factory() as session, session.begin():
        return await create_tenant(session, f"bt-{uuid.uuid4().hex[:6]}", country="IN")


def container(session_factory, tmp_path, scope: TenantScope, now=FREEZE_AT + timedelta(days=1)) -> Container:
    inputs = build_inputs()
    frozen_snapshot = snapshot(inputs, scope.client_id).model_copy(
        update={"workspace_id": scope.workspace_id}
    )
    settings = make_settings(
        evaluation={
            "bundle_dir": str(tmp_path),
            "backtest": {"random_replicates": 20, "bootstrap_resamples": 200},
        }
    )
    return Container(
        settings=settings,
        session_factory=session_factory,
        clock=FrozenClock(now),
        adapters={
            "run_config_source": FixedConfigSource(frozen_snapshot),
            "analytics_inputs": StaticSource(inputs),
        },
    )


def context(deps: Container, scope: TenantScope, artifacts: dict[str, Any], run_id: uuid.UUID) -> RunContext:
    return RunContext(
        run_id=run_id,
        kind=RunKind.backtest,
        trigger=RunTrigger.evaluation,
        attempt=1,
        as_of=FREEZE_AT,
        snapshot=deps.adapters["run_config_source"].value,
        clock=FrozenClock(FREEZE_AT),
        budget=Ledger(),
        reporter=Reporter(),
        artifacts=Artifacts(artifacts),
        cancel=NoCancel(),
        deps=deps,
    )


async def test_freeze_job_writes_bundle_and_row_and_refuses_mid_run(session_factory, tmp_path) -> None:
    scope = await tenant(session_factory)
    deps = container(session_factory, tmp_path, scope)
    manifest = await FreezeJob(deps).run(scope.client_id, FREEZE_AT)
    assert manifest.pit_level == "approximate" and "videos_fetched" in manifest.late_tables
    assert manifest.expires_at == FREEZE_AT + timedelta(days=28)
    loaded = BundleStore(tmp_path, timedelta(days=28)).read(manifest.freeze_id, FREEZE_AT + timedelta(days=1))
    assert all(v.published_at <= FREEZE_AT for v in loaded.inputs.videos)
    async with session_factory() as session:
        row = await FreezeRepository(session).at(scope.client_id, FREEZE_AT)
    assert row.freeze_id == manifest.freeze_id and row.record_counts["videos"] == len(loaded.inputs.videos)
    async with session_factory() as session, session.begin():
        await insert_row(
            session,
            PipelineRun.__table__,
            scope,
            {"kind": "analysis", "status": "running", "config_hash": "h"},
        )
    with pytest.raises(FreezeRefused):
        await FreezeJob(deps).run(scope.client_id, FREEZE_AT)
    with pytest.raises(FreezeRefused):
        await FreezeJob(deps).run(uuid.uuid4())


async def recorded(session_factory, tmp_path):
    scope = await tenant(session_factory)
    deps = container(session_factory, tmp_path, scope)
    await FreezeJob(deps).run(scope.client_id, FREEZE_AT)
    run_id = uuid.uuid4()
    corpus = await FrozenCorpusStage().run(context(deps, scope, {}, run_id))
    card = idea(1)
    output = output_with([card]).model_copy(update={"client_id": scope.client_id})
    artifacts = {FROZEN_CORPUS.name: corpus.artifact, RECOMMEND_ARTIFACT.name: output}
    record = await RecordBacktestStage().run(context(deps, scope, artifacts, run_id))
    return scope, deps, run_id, corpus, record


async def test_backtest_stages_check_the_canary_and_register_every_arm(session_factory, tmp_path) -> None:
    scope, _, run_id, corpus, record = await recorded(session_factory, tmp_path)
    assert corpus.artifact.canary_passed and corpus.artifact.canary_planted_rows > 0
    assert record.artifact.eval_id == eval_id_for(run_id)
    assert record.artifact.specs_by_arm == {"system": 1, "copy_top": 1, "random": 20}
    assert record.artifact.outcomes_mature_at == FREEZE_AT + timedelta(days=18)
    async with session_factory() as session:
        row = await session.get(EvalConfig, eval_id_for(run_id))
        stored = (
            await session.execute(
                select(func.count())
                .select_from(EvalPatternSpec)
                .where(EvalPatternSpec.eval_id == row.eval_id)
            )
        ).scalar_one()
    assert row.status == "registered" and stored == 22
    assert row.config["client_id"] == str(scope.client_id)


async def test_record_is_idempotent_and_a_changed_config_is_refused(session_factory, tmp_path) -> None:
    scope, deps, run_id, corpus, _ = await recorded(session_factory, tmp_path)
    artifacts = {FROZEN_CORPUS.name: corpus.artifact, RECOMMEND_ARTIFACT.name: output_with([idea(1)])}
    again = await RecordBacktestStage().run(context(deps, scope, artifacts, run_id))
    assert again.artifact.eval_id == eval_id_for(run_id)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(EvalConfig).where(EvalConfig.eval_id == eval_id_for(run_id)).values(config_hash="tampered")
        )
    async with session_factory() as session:
        with pytest.raises(ConfigChanged):
            await BacktestStore(session).config(eval_id_for(run_id))


async def test_scoring_waits_for_mature_outcomes_then_persists_results(session_factory, tmp_path) -> None:
    scope, deps, run_id, _, _ = await recorded(session_factory, tmp_path)
    runner = BacktestRunner(deps)
    eval_id = eval_id_for(run_id)
    with pytest.raises(OutcomesNotMature):
        await runner.score([eval_id], FREEZE_AT + timedelta(days=5))
    report = await runner.score([eval_id], FREEZE_AT + timedelta(days=19))
    # the golden snapshots stop at its as_of, so 40% of post-T videos lack day 7 and the run is invalid
    assert report.verdict == "invalid"
    assert report.missingness["complete"] > 0
    assert any("lack a day-a measurement" in item for item in report.limitations)
    assert sum(report.missingness.values()) > 0
    async with session_factory() as session:
        status = (await session.get(EvalConfig, eval_id)).status
        outcomes = (
            await session.execute(
                select(func.count()).select_from(EvalOutcomeVideo).where(EvalOutcomeVideo.eval_id == eval_id)
            )
        ).scalar_one()
        verdict = (
            await session.execute(
                select(EvalResult.notes).where(EvalResult.eval_id == eval_id, EvalResult.metric == "verdict")
            )
        ).scalar_one()
    assert status == "scored" and outcomes == sum(report.missingness.values()) and verdict == report.verdict
    payload = await BacktestApi(deps).backtest_report(scope, run_id)
    assert payload["status"] == "scored" and payload["specs_by_arm"]["random"] == 20
    with pytest.raises(NotFound):
        await BacktestApi(deps).backtest_report(
            TenantScope(workspace_id=uuid.uuid4(), client_id=None), run_id
        )


async def test_nightly_purge_deletes_expired_bundles_and_orphans(session_factory, tmp_path) -> None:
    scope = await tenant(session_factory)
    deps = container(session_factory, tmp_path, scope)
    manifest = await FreezeJob(deps).run(scope.client_id, FREEZE_AT)
    store = BundleStore(tmp_path, timedelta(days=28))
    orphan = uuid.uuid4()
    store.path_for(orphan).mkdir(parents=True)
    result = await purge_expired_bundles(session_factory, store, manifest.expires_at + timedelta(seconds=1))
    assert result["bundles_deleted"] == 1 and result["orphans_deleted"] == 1
    assert store.stored_ids() == []
    async with session_factory() as session:
        row = await session.get(EvalFreeze, manifest.freeze_id)
    assert row.status == "purged" and row.bundle_uri is None and row.channel_ids == []
