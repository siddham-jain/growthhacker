import uuid

import pytest

from app.adapters.embeddings.router import EmbedderRouter
from app.core.clock import FrozenClock
from app.domain.runs import RunKind, RunTrigger
from app.pipeline.contracts import (
    BudgetExceeded,
    BudgetKind,
    Market,
    RunConfigSnapshot,
    RunContext,
    UsageLedger,
)
from app.pipeline.stages.niche import NICHE_DISCOVERY, NicheChannel, NicheDiscovery
from app.pipeline.stages.topics import TOPIC_REPORT, LedgerSpendGuard, TopicsStage
from app.services.topics.engine import TopicEngine
from tests.conftest import make_settings
from tests.topics.fixtures import CHANNEL, NOW, FakeLLM, HashEmbedder, judge_by_label
from tests.topics.golden import apollo

pytestmark = pytest.mark.db


class Ledger:
    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.spent = 0.0

    def ensure(self, kind: BudgetKind, amount: float) -> None:
        if self.spent + amount > self.limit:
            raise BudgetExceeded(kind, amount, self.limit - self.spent)

    def charge(self, kind: BudgetKind, amount: float) -> None:
        self.ensure(kind, amount)
        self.spent += amount

    def remaining(self, kind: BudgetKind) -> float | None:
        return self.limit - self.spent

    def usage(self) -> UsageLedger:
        return UsageLedger(llm_usd=self.spent)


class Reporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def step(self, step, message, payload=None) -> None:
        self.events.append(("step", message))

    async def progress(self, done, total, unit) -> None:
        self.events.append(("progress", unit))

    async def insight(self, message, payload) -> None:
        self.events.append(("insight", message))

    async def notice(self, notice) -> None:
        self.events.append(("notice", notice.message))


class Artifacts:
    def __init__(self, niche=None) -> None:
        self.niche = niche

    async def get_optional(self, key):
        return self.niche if key is NICHE_DISCOVERY else None


class Cancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


class Container:
    def __init__(self, uow_factory, llm) -> None:
        self.settings = make_settings()
        self.clock = FrozenClock(NOW)
        self.uow = uow_factory
        embedder = HashEmbedder()
        self._ports = {
            "embedder": EmbedderRouter({"hash": embedder}, {"Latn": ["hash"], "Deva": ["hash"]}, "hash"),
            "llm": llm,
        }

    def adapter(self, port: str):
        if port not in self._ports:
            raise LookupError(port)
        return self._ports[port]


def context(
    scope, container, refs, kind=RunKind.analysis, ledger=None, own_channel=CHANNEL, niche=None
) -> RunContext:
    snapshot = RunConfigSnapshot(
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        config_version=1,
        config_hash="c",
        window_days=30,
        own_channel_id=own_channel,
        topic_refs=refs,
        topic_set_version=1,
        profile_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    return RunContext(
        run_id=uuid.uuid4(),
        kind=kind,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot,
        clock=FrozenClock(NOW),
        budget=ledger or Ledger(1.0),
        reporter=Reporter(),
        artifacts=Artifacts(niche),
        cancel=Cancel(),
        deps=container,
        stage="topics",
    )


async def test_stage_matches_pinned_refs_and_reports(scope, uow_factory, video_ids) -> None:
    llm = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    container = Container(uow_factory, llm)
    engine = TopicEngine.from_container(container)
    build = await engine.build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    stage = TopicsStage()
    ctx = context(scope, container, build.universe.refs)
    assert stage.enabled(ctx.snapshot)
    assert not stage.enabled(ctx.snapshot.model_copy(update={"topic_refs": []}))
    assert stage.fingerprint_inputs(ctx)["topic_set_version"] == 1
    result = await stage.run(ctx)
    assert result.status == "succeeded"
    assert result.artifact.videos_considered == len(video_ids)
    assert result.counts["matched"] == result.artifact.videos_matched
    assert any(kind == "insight" for kind, _ in ctx.reporter.events)
    assert TOPIC_REPORT.name == "topics" and not TOPIC_REPORT.contains_api_data
    assert ctx.budget.spent == pytest.approx(0.0015)


async def test_stage_degrades_when_the_llm_budget_is_gone(scope, uow_factory, video_ids) -> None:
    llm = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    container = Container(uow_factory, llm)
    build = await TopicEngine.from_container(container).build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    calls_before = len(llm.calls)
    ctx = context(scope, container, build.universe.refs, kind=RunKind.daily_refresh, ledger=Ledger(0.0))
    result = await TopicsStage().run(ctx)
    assert result.status == "degraded"
    assert "llm_budget" in result.artifact.degraded_modes
    assert [notice.code.value for notice in result.notices] == ["llm_fallback"]
    assert len(llm.calls) == calls_before


def test_spend_guard_maps_ledger_refusals() -> None:
    guard = LedgerSpendGuard(Ledger(0.01))
    assert guard.allow(0.005)
    guard.record(0.008)
    assert not guard.allow(0.005)


class WorkspaceDeps:
    """per-run deps: the shared container plus this workspace's llm client."""

    def __init__(self, shared: Container, llm) -> None:
        self.shared = shared
        self.llm = llm

    def __getattr__(self, name: str):
        return getattr(self.shared, name)


async def test_stage_adjudicates_with_the_workspace_llm(scope, uow_factory, video_ids) -> None:
    deployment = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    workspace = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    container = Container(uow_factory, deployment)
    build = await TopicEngine.from_container(container).build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    deployment_calls = len(deployment.calls)
    ctx = context(scope, WorkspaceDeps(container, workspace), build.universe.refs)
    result = await TopicsStage().run(ctx)
    assert result.counts["llm_calls"] > 0
    assert len(workspace.calls) == result.counts["llm_calls"]
    assert len(deployment.calls) == deployment_calls


async def test_stage_matches_niche_and_creator_uploads(scope, uow_factory, video_ids) -> None:
    llm = FakeLLM(judge=judge_by_label(apollo.ADJUDICATION_RULES), proposals=apollo.PROPOSALS)
    container = Container(uow_factory, llm)
    build = await TopicEngine.from_container(container).build_universe(
        scope, apollo.PROFILE.model_copy(update={"client_id": scope.client_id}), apollo.QUESTIONS
    )
    alone = await TopicsStage().run(context(scope, container, build.universe.refs, own_channel=None))
    assert alone.artifact.videos_considered == 0
    niche = NicheDiscovery(channels=[NicheChannel(channel_id=CHANNEL, role="creator", status="active")])
    result = await TopicsStage().run(
        context(scope, container, build.universe.refs, own_channel=None, niche=niche)
    )
    assert result.artifact.videos_considered == len(video_ids)
