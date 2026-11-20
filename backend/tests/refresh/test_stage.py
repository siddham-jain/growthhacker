from dataclasses import dataclass, field
from typing import Any

from app.core.clock import FrozenClock
from app.core.config import RefreshSettings
from app.core.registry import artifact_schema_registry, quality_gate_registry, stage_registry
from app.domain.common import DomainModel
from app.domain.recommendations import GateAction
from app.domain.refresh import InsertionKind
from app.domain.runs import RunKind, RunTrigger
from app.pipeline.contracts import BudgetKind, Market, RunConfigSnapshot, RunContext, UsageLedger
from app.pipeline.stages.refresh import REFRESH_ARTIFACT, RefreshStage
from app.services.refresh.gates import AdditiveDiffOnly, MinimalEdit
from app.services.refresh.output import RefreshOutput
from tests.refresh import scenarios
from tests.refresh.fixtures import CLIENT_ID, NOW, RUN_ID, FakeProbe


class Reporter:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str]] = []
        self.insights: list[Any] = []

    async def step(self, step: str, message: str, payload=None) -> None:
        self.steps.append((step, message))

    async def progress(self, done: int, total: int | None, unit: str) -> None:
        return None

    async def insight(self, message: str, payload) -> None:
        self.insights.append(payload)

    async def notice(self, notice) -> None:
        return None


class Budget:
    def ensure(self, kind: BudgetKind, amount: float) -> None:
        return None

    def charge(self, kind: BudgetKind, amount: float) -> None:
        return None

    def remaining(self, kind: BudgetKind) -> float | None:
        return None

    def usage(self) -> UsageLedger:
        return UsageLedger()


class Cancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


class Artifacts:
    async def get(self, key):
        raise KeyError(key.name)

    async def get_optional(self, key):
        return None


class Inputs:
    def __init__(self, data) -> None:
        self.data = data

    async def load(self, ctx):
        return self.data


class MemoryStore:
    def __init__(self) -> None:
        self.saved: list[tuple] = []

    async def load_history(self, since):
        return [], [], None

    async def save_run(self, refreshes, eligibility, changes, impacts, last_keyword_eval_at) -> None:
        self.saved.append((list(refreshes), eligibility, list(changes), list(impacts), last_keyword_eval_at))


@dataclass
class Deps:
    refresh_inputs: Inputs
    refresh_store: MemoryStore | None = field(default_factory=MemoryStore)
    refresh_settings: RefreshSettings = field(default_factory=RefreshSettings)
    link_probe: FakeProbe | None = field(default_factory=FakeProbe)
    link_cache: Any = None
    llm: Any = None
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(NOW))


def snapshot(**overrides) -> RunConfigSnapshot:
    fields = {
        "workspace_id": CLIENT_ID,
        "client_id": CLIENT_ID,
        "config_version": 1,
        "config_hash": "h",
        "window_days": 30,
        "own_channel_id": "UCownChannel000000000009",
        "topic_set_version": 1,
        "market": Market(country="IN", language="en", timezone="Asia/Kolkata"),
    }
    return RunConfigSnapshot(**{**fields, **overrides})


def context(deps: Deps, reporter: Reporter) -> RunContext[Deps]:
    return RunContext(
        run_id=RUN_ID,
        kind=RunKind.daily_refresh,
        trigger=RunTrigger.schedule,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot(),
        clock=deps.clock,
        budget=Budget(),
        reporter=reporter,
        artifacts=Artifacts(),
        cancel=Cancel(),
        deps=deps,
    )


def test_stage_is_registered_with_its_artifact() -> None:
    assert stage_registry.get("refresh") is RefreshStage
    assert artifact_schema_registry.model("refresh", 1) is RefreshOutput
    assert REFRESH_ARTIFACT.contains_api_data
    assert "AdditiveDiffOnly" in quality_gate_registry and "MinimalEdit" in quality_gate_registry
    stage = RefreshStage()
    assert stage.enabled(snapshot())
    assert not stage.enabled(snapshot(own_channel_id=None))
    assert not stage.enabled(snapshot(feature_flags={"description_refresh": False}))


async def test_stage_runs_persists_and_reports() -> None:
    deps = Deps(refresh_inputs=Inputs(scenarios.runable()))
    reporter = Reporter()
    result = await RefreshStage().run(context(deps, reporter))
    assert result.status == "succeeded"
    assert isinstance(result.artifact, RefreshOutput)
    assert result.counts["drafts"] == 1
    saved_refreshes, _, _, _, keyword_eval_at = deps.refresh_store.saved[0]
    assert saved_refreshes == result.artifact.refreshes
    assert keyword_eval_at == NOW
    assert reporter.steps[0][0] == "refresh.decision"
    assert any(payload.video_id == scenarios.AI_VIDEO for payload in reporter.insights)
    assert RefreshStage().fingerprint_inputs(context(deps, reporter))["as_of_day"] == "2026-11-20"


async def test_unverified_channel_skips_the_stage() -> None:
    data = scenarios.runable().model_copy(update={"channel_verified": False})
    reporter = Reporter()
    result = await RefreshStage().run(context(Deps(refresh_inputs=Inputs(data)), reporter))
    assert result.status == "skipped"
    assert reporter.steps == [("refresh.skipped", "Confirm your channel to get refresh suggestions.")]


async def test_gates_reject_rewrites_dressed_up_as_link_fixes(runable_output) -> None:
    draft = runable_output.recommendation_drafts[0]
    assert AdditiveDiffOnly().check(draft).action is GateAction.pass_
    assert MinimalEdit().check(draft).action is GateAction.pass_
    op = draft.subject.operations[0]
    tampered_op = op.model_copy(
        update={
            "kind": InsertionKind.link_fix,
            "old_text": "In this video I share my exact workflow",
            "text": "In this video I share my exact Claude Fable 5.2 workflow",
        }
    )
    tampered = draft.model_copy(
        update={"subject": draft.subject.model_copy(update={"operations": [tampered_op]})}
    )
    result = AdditiveDiffOnly().check(tampered)
    assert (result.action, result.reason_code) == (GateAction.suppress, "REFRESH_NOT_ADDITIVE")
    oversized = draft.model_copy(
        update={
            "subject": draft.subject.model_copy(
                update={"operations": [op.model_copy(update={"keywords_added": ["a", "b", "c", "d"]})]}
            )
        }
    )
    assert MinimalEdit().check(oversized).reason_code == "REFRESH_NOT_MINIMAL"


def test_output_is_json_round_trippable(runable_output) -> None:
    dumped = runable_output.model_dump(mode="json")
    assert RefreshOutput.model_validate(dumped) == runable_output
    assert isinstance(runable_output, DomainModel)
