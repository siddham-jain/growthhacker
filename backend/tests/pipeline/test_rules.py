from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.domain.runs import ClientLifecycle, RunEventType, RunKind, RunStatus, StageStatus
from app.models.runs import PipelineRun
from app.pipeline.client_settings import ClientSettings
from app.pipeline.context import RunBudgetLedger
from app.pipeline.contracts import BudgetExceeded, BudgetKind, RunBudget, StageBudget, UsageLedger
from app.pipeline.fingerprint import as_of_bucket, stage_fingerprint
from app.pipeline.lifecycle import ClientLifecycleMachine, RunStateMachine, StageStateMachine, TransitionError
from app.pipeline.progress import ProgressTracker, RunProgress
from app.pipeline.registry import PipelineConfigError
from app.pipeline.runs import _needs_full_recrawl, dedupe_decision
from app.pipeline.schedules import jitter_minutes, next_daily_due
from app.workers.scheduler import DueClient, round_robin
from tests.conftest import make_settings
from tests.pipeline.fakes import Probe, catalog, fake_stage, pipeline

PT = ZoneInfo("America/Los_Angeles")


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (RunStatus.queued, RunStatus.running, True),
        (RunStatus.queued, RunStatus.cancelled, True),
        (RunStatus.queued, RunStatus.superseded, True),
        (RunStatus.running, RunStatus.running, True),
        (RunStatus.running, RunStatus.partial, True),
        (RunStatus.running, RunStatus.cancelling, True),
        (RunStatus.cancelling, RunStatus.cancelled, True),
        (RunStatus.cancelling, RunStatus.succeeded, False),
        (RunStatus.queued, RunStatus.succeeded, False),
        (RunStatus.succeeded, RunStatus.running, False),
        (RunStatus.failed, RunStatus.queued, False),
    ],
)
def test_run_transition_table(current: RunStatus, target: RunStatus, allowed: bool) -> None:
    assert RunStateMachine.can(current, target) is allowed


def test_terminal_statuses_never_change() -> None:
    for status in RunStatus:
        if RunStateMachine.is_terminal(status):
            assert not any(RunStateMachine.can(status, target) for target in RunStatus)


def test_cas_sources_list_every_status_that_may_reach_a_target() -> None:
    assert RunStateMachine.sources(RunStatus.cancelled) == ("cancelling", "queued", "running")
    assert RunStateMachine.sources(RunStatus.succeeded) == ("running",)


def test_stage_transitions_only_move_forward() -> None:
    StageStateMachine.ensure(StageStatus.pending, StageStatus.running)
    StageStateMachine.ensure(StageStatus.running, StageStatus.interrupted)
    with pytest.raises(TransitionError):
        StageStateMachine.ensure(StageStatus.succeeded, StageStatus.running)
    with pytest.raises(TransitionError):
        StageStateMachine.ensure(StageStatus.pending, StageStatus.succeeded)


@pytest.mark.parametrize(
    ("current", "kind", "status", "expected"),
    [
        (
            ClientLifecycle.onboarding,
            RunKind.onboarding,
            RunStatus.succeeded,
            ClientLifecycle.awaiting_confirmation,
        ),
        (ClientLifecycle.onboarding, RunKind.onboarding, RunStatus.failed, ClientLifecycle.onboarding_failed),
        (ClientLifecycle.analyzing, RunKind.analysis, RunStatus.partial, ClientLifecycle.active),
        (
            ClientLifecycle.analyzing,
            RunKind.analysis,
            RunStatus.failed,
            ClientLifecycle.awaiting_confirmation,
        ),
        (ClientLifecycle.active, RunKind.daily_refresh, RunStatus.failed, None),
    ],
)
def test_client_lifecycle_follows_runs(
    current: ClientLifecycle, kind: RunKind, status: RunStatus, expected: ClientLifecycle | None
) -> None:
    assert ClientLifecycleMachine.on_run_finished(current, kind, status) is expected


def test_client_lifecycle_start_and_manual_moves() -> None:
    assert (
        ClientLifecycleMachine.on_run_started(ClientLifecycle.created, RunKind.onboarding)
        is ClientLifecycle.onboarding
    )
    assert (
        ClientLifecycleMachine.on_run_started(ClientLifecycle.awaiting_confirmation, RunKind.analysis)
        is ClientLifecycle.analyzing
    )
    assert ClientLifecycleMachine.can(ClientLifecycle.active, ClientLifecycle.paused)
    assert not ClientLifecycleMachine.can(ClientLifecycle.deleting, ClientLifecycle.active)


@pytest.mark.parametrize(
    ("active_kind", "active_status", "same_config", "requested", "expected"),
    [
        (RunKind.analysis, RunStatus.running, True, RunKind.analysis, "join"),
        (RunKind.analysis, RunStatus.running, True, RunKind.daily_refresh, "join"),
        (RunKind.daily_refresh, RunStatus.queued, True, RunKind.analysis, "supersede"),
        (RunKind.daily_refresh, RunStatus.running, False, RunKind.analysis, "follow_up"),
        (RunKind.analysis, RunStatus.running, False, RunKind.analysis, "follow_up"),
        (RunKind.daily_refresh, RunStatus.running, True, RunKind.analysis, "conflict"),
        (RunKind.onboarding, RunStatus.running, True, RunKind.analysis, "conflict"),
        (RunKind.analysis, RunStatus.cancelling, True, RunKind.analysis, "cancelling"),
    ],
)
def test_dedupe_decisions(
    active_kind: RunKind, active_status: RunStatus, same_config: bool, requested: RunKind, expected: str
) -> None:
    decision = dedupe_decision(
        active_kind=active_kind,
        active_status=active_status,
        active_config_hash="a",
        requested_kind=requested,
        requested_config_hash="a" if same_config else "b",
    )
    assert decision == expected


def test_fingerprint_is_stable_and_version_sensitive() -> None:
    base = {
        "name": "topics",
        "schema_version": 1,
        "upstream": {"b": "2", "a": "1"},
        "inputs": {"x": 1},
        "bucket": None,
    }
    first = stage_fingerprint(version=1, **base)
    assert first == stage_fingerprint(version=1, **{**base, "upstream": {"a": "1", "b": "2"}})
    assert first != stage_fingerprint(version=2, **base)
    assert first != stage_fingerprint(version=1, **{**base, "inputs": {"x": 2}})


def test_as_of_bucket_follows_the_pacific_quota_day() -> None:
    late = datetime(2026, 11, 20, 7, 59, tzinfo=UTC)
    after_midnight_pt = late + timedelta(minutes=2)
    assert as_of_bucket(late, 6, PT) != as_of_bucket(after_midnight_pt, 6, PT)
    assert as_of_bucket(late, None, PT) is None
    assert as_of_bucket(late, 168, PT) == as_of_bucket(late + timedelta(hours=1), 168, PT)


def test_registry_reports_unregistered_stages_and_cycles() -> None:
    probe = Probe()
    stages = [fake_stage("a", probe, deps=("b",)), fake_stage("b", probe, deps=("a",))]
    result = catalog(stages, [pipeline(("a", "b")), pipeline(("a", "missing"), name="partial")])
    assert "analysis" in result.unavailable and "cycle" in result.unavailable["analysis"][0]
    assert result.unavailable["partial"] == ("unregistered stage 'missing'",)
    assert not result.available


def test_registry_rejects_dependencies_outside_the_pipeline() -> None:
    probe = Probe()
    result = catalog([fake_stage("a", probe), fake_stage("b", probe, deps=("a",))], [pipeline(("b",))])
    assert "outside the pipeline" in result.unavailable["analysis"][0]


def test_registry_aborts_on_a_bad_stage() -> None:
    probe = Probe()
    with pytest.raises(PipelineConfigError, match="weight"):
        catalog([fake_stage("a", probe, weight=0)], [pipeline(("a",))])


def test_budget_ledger_enforces_stage_and_run_limits() -> None:
    run = RunBudgetLedger(
        RunBudget(wall_s=60, youtube_units=100, search_calls=1, llm_usd=0.5, tavily_credits=0),
        UsageLedger(youtube_units=30),
    )
    stage = run.for_stage(StageBudget(youtube_units=50))
    stage.charge(BudgetKind.youtube_units, 50)
    with pytest.raises(BudgetExceeded):
        stage.charge(BudgetKind.youtube_units, 1)
    other = run.for_stage(StageBudget())
    assert other.remaining(BudgetKind.youtube_units) == 20
    with pytest.raises(BudgetExceeded):
        other.ensure(BudgetKind.youtube_units, 21)
    assert other.remaining(BudgetKind.llm_calls) is None
    assert run.usage().youtube_units == 80


class _RecordingLog:
    def __init__(self) -> None:
        self.types: list[str] = []

    async def append(self, run_id, values, *, progress):
        self.types.extend(value["type"] for value in values)
        return []


async def test_a_terminal_event_is_never_followed_by_a_percent_tick() -> None:
    now = [0.0]
    tracker = ProgressTracker({"a": (1.0, 10.0)}, monotonic=lambda: now[0])
    log = _RecordingLog()
    progress = RunProgress(
        run_id=uuid4(),
        client_id=uuid4(),
        log=log,
        publisher=None,
        prefix="t",
        settings=make_settings().pipeline,
        tracker=tracker,
        monotonic=lambda: now[0],
    )
    tracker.start("a")
    now[0] = 1000.0
    await progress.emit(RunEventType.run_cancelled, "Cancelled", status=RunStatus.cancelled)

    assert log.types == [RunEventType.run_progress.value, RunEventType.run_cancelled.value]


def test_progress_is_monotonic_and_asymptotic() -> None:
    now = [0.0]
    tracker = ProgressTracker({"a": (1.0, 10.0), "b": (3.0, 10.0)}, monotonic=lambda: now[0])
    tracker.start("a")
    now[0] = 1000.0
    assert tracker.percent() == pytest.approx(22.5)
    tracker.fraction("a", 1, 10)
    assert tracker.percent() == pytest.approx(22.5)
    tracker.finish("a")
    tracker.finish("b")
    assert tracker.percent() == 100.0


def test_daily_due_time_respects_local_hour_and_dst() -> None:
    new_york = ZoneInfo("America/New_York")
    before_spring_forward = datetime(2026, 3, 7, 12, tzinfo=UTC)
    due = next_daily_due(before_spring_forward, tz=new_york, hour=6, jitter=10)
    assert due.astimezone(new_york).hour == 6 and due.astimezone(new_york).minute == 10
    assert due > before_spring_forward
    client = UUID(int=42)
    assert jitter_minutes(client, 40) == jitter_minutes(client, 40) < 40


def test_round_robin_interleaves_workspaces_least_recently_served_first() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)

    def due(workspace: int, client: int, served: datetime | None) -> DueClient:
        return DueClient(
            workspace_id=UUID(int=workspace),
            client_id=UUID(int=client),
            timezone="UTC",
            settings=ClientSettings(),
            next_due_at=now - timedelta(minutes=client),
            cadence_hours=24,
            last_enqueued_at=served,
        )

    order = round_robin(
        [due(1, 10, now), due(1, 11, now), due(1, 12, now), due(2, 20, None), due(2, 21, None)]
    )
    assert [item.workspace_id.int for item in order] == [2, 1, 2, 1, 1]


@pytest.mark.parametrize(
    ("kind", "confirm", "website", "expected"),
    [
        (RunKind.analysis, True, "https://smiledental.in", True),
        (RunKind.analysis, False, "https://smiledental.in", False),
        (RunKind.analysis, True, None, False),
        (RunKind.daily_refresh, True, "https://smiledental.in", False),
    ],
)
def test_only_the_first_confirmed_analysis_queues_the_full_recrawl(kind, confirm, website, expected) -> None:
    run = PipelineRun(
        kind=kind.value, params={"confirm_setup": confirm}, config_snapshot={"website_url": website}
    )
    assert _needs_full_recrawl(run) is expected
