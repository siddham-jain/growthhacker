from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.config import RecommendSettings
from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunNoticeCode, RunTrigger
from app.pipeline.contracts import Market, RunConfigSnapshot, RunContext
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT, RecommendStage
from app.ports.llm import LLMError, LLMErrorKind
from app.services.recommend.inputs import RecommendInputs
from tests.recommend.fixtures import AS_OF, CLIENT_ID, GENERATED_AT, RUN_ID, apollo_inputs


class FixedClock:
    def now(self) -> datetime:
        return GENERATED_AT

    def today(self, tz):
        return GENERATED_AT.astimezone(tz).date()


class NoCancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


@dataclass
class FixtureInputs:
    inputs: RecommendInputs

    async def load(self, client_id: UUID, as_of: datetime, window_days: int) -> RecommendInputs:
        assert (client_id, as_of, window_days) == (CLIENT_ID, AS_OF, self.inputs.selected_window)
        return self.inputs


@dataclass
class MemoryStore:
    saved: list = field(default_factory=list)

    async def save_run(self, scope: TenantScope, run_id: UUID, run, own) -> int:
        self.saved.append((scope, run_id, len(run.cards)))
        return 0


class BrokenWriter:
    async def write(self, requests):
        raise LLMError(LLMErrorKind.timeout, "deadline")


@dataclass
class Deps:
    recommend_settings: RecommendSettings
    recommend_inputs: FixtureInputs
    card_writer: object | None = None
    recommendation_store: MemoryStore | None = None


def context(deps: Deps) -> RunContext[Deps]:
    snapshot = RunConfigSnapshot(
        workspace_id=UUID(int=1),
        client_id=CLIENT_ID,
        config_version=1,
        config_hash="cfg",
        window_days=deps.recommend_inputs.inputs.selected_window,
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    return RunContext(
        run_id=RUN_ID,
        kind=RunKind.analysis,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=AS_OF,
        snapshot=snapshot,
        clock=FixedClock(),
        budget=None,
        reporter=None,
        artifacts=None,
        cancel=NoCancel(),
        deps=deps,
    )


def test_stage_is_registered_under_plan_11s_name() -> None:
    assert stage_registry.get("recommend") is RecommendStage
    assert RecommendStage.depends_on == ("topics", "analytics")
    assert RECOMMEND_ARTIFACT.contains_api_data


async def test_stage_produces_all_windows_and_persists_through_the_store() -> None:
    store = MemoryStore()
    deps = Deps(RecommendSettings(), FixtureInputs(apollo_inputs()), recommendation_store=store)
    result = await RecommendStage().run(context(deps))

    assert result.status == "succeeded"
    assert [window.set.window_days for window in result.artifact.windows] == [7, 30, 90]
    assert result.counts["cards_shown"] == len(result.artifact.window(30).set.recommendations) >= 5
    assert store.saved and store.saved[0][1] == RUN_ID
    assert len(result.artifact.model_dump_json()) < 2 * 1024 * 1024


async def test_writer_outage_degrades_the_stage_with_a_notice() -> None:
    deps = Deps(RecommendSettings(), FixtureInputs(apollo_inputs()), card_writer=BrokenWriter())
    result = await RecommendStage().run(context(deps))

    assert result.status == "degraded"
    assert RunNoticeCode.llm_fallback in {notice.code for notice in result.notices}
    assert {card.extras.rendered_by for card in result.artifact.window(30).set.recommendations} == {
        "template"
    }


async def test_thin_selected_window_raises_a_thin_data_notice() -> None:
    deps = Deps(RecommendSettings(), FixtureInputs(apollo_inputs(selected_window=7)))
    result = await RecommendStage().run(context(deps))

    thin = [notice for notice in result.notices if notice.code is RunNoticeCode.thin_data]
    assert thin and thin[0].suggest == {"widen_window_to": 30}
