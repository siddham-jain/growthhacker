"""the real composition root: registered adapters, the backtest dag and the cli, with no network."""

import pytest
from sqlalchemy import delete

import app.evaluation.registrations  # noqa: F401 - registers 12's cron tasks and services
from app.bootstrap import load_registrations
from app.core.container import build_container
from app.evaluation.cli import build_parser
from app.evaluation.freeze.job import FreezeJob
from app.models.evaluation import EvalFreeze
from app.models.tenancy import Workspace
from app.pipeline.registry import validate_pipelines
from app.workers.tasks import OPS_ALERTS, OPS_ROLLUP, maintenance_task_registry
from tests.conftest import make_settings
from tests.foundation.factories import create_tenant

pytestmark = pytest.mark.db


def test_backtest_dag_is_complete_and_maintenance_tasks_are_registered() -> None:
    load_registrations()
    catalog = validate_pipelines()
    assert "backtest" in catalog.available
    assert {"frozen_corpus", "record_backtest"} <= set(catalog.stages)
    assert OPS_ROLLUP in maintenance_task_registry and OPS_ALERTS in maintenance_task_registry


def test_cli_exposes_every_group() -> None:
    parser = build_parser()
    for argv in (
        ["freeze", "create", "--all"],
        ["backtest", "score", "--eval-id", "00000000-0000-0000-0000-000000000001"],
        ["labels", "guidelines"],
        ["review", "rubric"],
        ["ops", "report", "--days", "1"],
        ["policy", "render"],
    ):
        assert parser.parse_args(argv).handler is not None


async def test_freeze_job_runs_through_the_registered_adapters(migrated_database, tmp_path) -> None:
    load_registrations()
    container = build_container(make_settings(evaluation={"bundle_dir": str(tmp_path)}))
    try:
        async with container.session_factory() as session, session.begin():
            scope = await create_tenant(session, "wiring", country="IN")
        manifest = await FreezeJob(container).run(scope.client_id)
        assert manifest.pit_level == "strict" and manifest.record_counts["videos"] == 0
        assert (tmp_path / str(manifest.freeze_id) / "manifest.json").exists()
    finally:
        async with container.session_factory() as session, session.begin():
            await session.execute(delete(EvalFreeze).where(EvalFreeze.client_id == scope.client_id))
            await session.execute(delete(Workspace).where(Workspace.id == scope.workspace_id))
        await container.aclose()
