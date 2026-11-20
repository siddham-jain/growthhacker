import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta

from app.core.clock import FrozenClock
from app.core.registry import stage_registry
from app.domain.runs import RunKind, RunTrigger
from app.pipeline.contracts import BudgetKind, Market, RunConfigSnapshot, RunContext, UsageLedger
from app.pipeline.stages.news import NEWS_ARTIFACT, NewsStage
from app.services.news.service import NewsRadarReport
from tests.news.fixtures import NOW, WORKSPACE_ID, runable_context
from tests.news.test_golden_runable import run


class Ledger:
    def __init__(self) -> None:
        self.charged: dict[BudgetKind, float] = {}

    def ensure(self, kind, amount) -> None:
        return None

    def charge(self, kind, amount) -> None:
        self.charged[kind] = self.charged.get(kind, 0) + amount

    def remaining(self, kind):
        return None

    def usage(self) -> UsageLedger:
        return UsageLedger()


class Reporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def step(self, step, message, payload=None) -> None:
        self.events.append(("step", step))

    async def progress(self, done, total, unit) -> None:
        return None

    async def insight(self, message, payload) -> None:
        self.events.append(("insight", payload))

    async def notice(self, notice) -> None:
        return None


class Cancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


@dataclass
class FakeRadar:
    report: NewsRadarReport
    calls: list[dict] = field(default_factory=list)

    async def scan(self, ctx, *, now, run_id=None, force=False):
        self.calls.append({"force": force, "now": now})
        return self.report


@dataclass
class Loader:
    context: object

    async def load(self, client_id, snapshot):
        return self.context


@dataclass
class Deps:
    news_radar: FakeRadar
    news_context: Loader


def run_context(deps, kind=RunKind.analysis, trigger=RunTrigger.schedule):
    snapshot = RunConfigSnapshot(
        workspace_id=WORKSPACE_ID,
        client_id=uuid.uuid4(),
        config_version=1,
        config_hash="x",
        window_days=30,
        topic_set_version=1,
        profile_version=3,
        market=Market(country="US", language="en", timezone="UTC"),
        freshness_profile=runable_context().freshness,
    )
    return RunContext(
        run_id=uuid.uuid4(),
        kind=kind,
        trigger=trigger,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot,
        clock=FrozenClock(NOW),
        budget=Ledger(),
        reporter=Reporter(),
        artifacts=None,
        cancel=Cancel(),
        deps=deps,
    )


async def report() -> NewsRadarReport:
    _ctx, outcome, _gw = await run(2, 1)
    return NewsRadarReport(
        client_id=uuid.uuid4(),
        as_of=NOW,
        profile_version=3,
        level="high",
        early_signal_drafts=outcome.drafts,
        llm_calls=2,
        tavily_credits=3.0,
    )


async def test_stage_is_registered_and_produces_the_news_artifact():
    assert stage_registry.get("news") is NewsStage
    deps = Deps(FakeRadar(await report()), Loader(runable_context()))
    ctx = run_context(deps)
    result = await NewsStage().run(ctx)
    assert result.status == "succeeded"
    assert result.artifact.early_signal_drafts[0].subject.key_entity == "Claude Fable 5.2"
    assert result.counts["early_signal_drafts"] == 1
    assert ctx.budget.charged == {BudgetKind.llm_calls: 2, BudgetKind.tavily_credits: 3.0}
    assert ctx.reporter.events[0] == ("step", "news_scan")
    assert ctx.reporter.events[1][1].headline == "Introducing Claude Fable 5.2"
    assert deps.news_radar.calls[0]["force"] is False
    assert NewsStage.produces is NEWS_ARTIFACT and NEWS_ARTIFACT.contains_api_data


async def test_user_news_scan_forces_and_missing_profile_skips():
    deps = Deps(FakeRadar(await report()), Loader(runable_context()))
    await NewsStage().run(run_context(deps, RunKind.news_scan, RunTrigger.user))
    assert deps.news_radar.calls[0]["force"] is True
    skipped = await NewsStage().run(run_context(Deps(deps.news_radar, Loader(None))))
    assert skipped.status == "skipped" and skipped.notices[0].code.value == "news_skipped"


def test_fingerprint_changes_per_cadence_slot_and_flag_disables():
    stage = NewsStage()
    ctx = run_context(Deps(None, Loader(None)))
    first = stage.fingerprint_inputs(ctx)
    same_slot = stage.fingerprint_inputs(replace(ctx, as_of=NOW + timedelta(minutes=30)))
    next_slot = stage.fingerprint_inputs(replace(ctx, as_of=NOW + timedelta(hours=7)))
    assert first["profile_version"] == 3
    assert first == same_slot and first != next_slot
    assert stage.enabled(ctx.snapshot)
    assert not stage.enabled(ctx.snapshot.model_copy(update={"feature_flags": {"news_radar": False}}))
