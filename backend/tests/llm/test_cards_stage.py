from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.adapters.llm.testing import FakeLLMClient
from app.core.clock import FrozenClock
from app.core.config import Settings
from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.card_copy import CardCopy
from app.domain.runs import CardWrittenPayload, RunKind, RunNoticeCode, RunTrigger
from app.pipeline.contracts import Market, RunConfigSnapshot, RunContext
from app.pipeline.stages.cards import CardInputs, WriteCardsStage
from app.ports.llm import LLMError, LLMErrorKind, TaskId
from app.services.llm.budgets import MemoryBudgetLedger
from app.services.llm.writer.schemas import WrittenCard
from app.services.llm.writer.service import build_card_writing_service
from tests.conftest import make_settings
from tests.llm.factories import (
    CLIENT_ID,
    GOOD_COPY,
    NOW,
    RUN_ID,
    WORKSPACE_ID,
    make_client_context,
    make_draft,
)


class Reporter:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, Any]] = []
        self.progress_calls: list[tuple[int, int | None, str]] = []

    async def step(self, step: str, message: str, payload: Any = None) -> None:
        self.steps.append((step, message, payload))

    async def progress(self, done: int, total: int | None, unit: str) -> None:
        self.progress_calls.append((done, total, unit))

    async def insight(self, message: str, payload: Any) -> None:
        raise AssertionError("write_cards emits no insights")

    async def notice(self, notice: Any) -> None:
        raise AssertionError("notices travel on the stage result")


class Inputs:
    def __init__(self, inputs: CardInputs) -> None:
        self._inputs = inputs

    async def load(self, ctx: Any) -> CardInputs:
        return self._inputs


class CopyStore:
    def __init__(self) -> None:
        self.saved: list[tuple[TenantScope, UUID, list[WrittenCard]]] = []

    async def save(self, scope: TenantScope, run_id: UUID, cards: list[WrittenCard]) -> None:
        self.saved.append((scope, run_id, cards))


class CardRows:
    def __init__(self) -> None:
        self.attached: list[tuple[TenantScope, UUID, Mapping[str, CardCopy]]] = []

    async def attach_copy(self, scope: TenantScope, run_id: UUID, copies: Mapping[str, CardCopy]) -> int:
        self.attached.append((scope, run_id, copies))
        return len(copies)


SOURCE_RUN_ID = uuid4()


@dataclass
class Deps:
    settings: Settings
    card_inputs: Inputs
    card_writing: Any
    llm_budget: MemoryBudgetLedger
    recommendation_copy: CopyStore = field(default_factory=CopyStore)
    recommendation_store: CardRows = field(default_factory=CardRows)


class Cancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


def context(deps: Deps, reporter: Reporter) -> RunContext[Deps]:
    snapshot = RunConfigSnapshot(
        workspace_id=WORKSPACE_ID,
        client_id=CLIENT_ID,
        config_version=1,
        config_hash="h",
        window_days=30,
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    return RunContext(
        run_id=RUN_ID,
        kind=RunKind.analysis,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot,
        clock=FrozenClock(NOW),
        budget=None,  # type: ignore[arg-type]
        reporter=reporter,
        artifacts=None,  # type: ignore[arg-type]
        cancel=Cancel(),
        deps=deps,
        stage="write_cards",
    )


def deps_for(llm: FakeLLMClient, drafts: list[Any]) -> Deps:
    settings = make_settings()
    return Deps(
        settings=settings,
        card_inputs=Inputs(
            CardInputs(drafts=drafts, client=make_client_context(), card_run_id=SOURCE_RUN_ID)
        ),
        card_writing=build_card_writing_service(
            llm, settings.llm.writer.model_copy(update={"critic_enabled": False})
        ),
        llm_budget=MemoryBudgetLedger(settings.llm.budgets),
    )


def test_stage_is_registered() -> None:
    assert stage_registry.get("write_cards") is WriteCardsStage


async def test_stage_writes_cards_and_streams_progress() -> None:
    drafts = [make_draft(), make_draft()]
    deps = deps_for(FakeLLMClient({TaskId.card_write: GOOD_COPY}), drafts)
    reporter = Reporter()
    result = await WriteCardsStage().run(context(deps, reporter))
    assert result.status == "succeeded"
    assert result.artifact is not None and result.artifact.llm_cards == 2
    assert result.notices == ()
    payloads = [payload for step, _, payload in reporter.steps if step == "write.card"]
    assert all(isinstance(payload, CardWrittenPayload) for payload in payloads)
    assert payloads[0].recommendation_id.startswith("rec_")
    assert reporter.progress_calls[-1] == (2, 2, "cards")
    scope, run_id, cards = deps.recommendation_copy.saved[0]
    assert scope.client_id == CLIENT_ID and run_id == RUN_ID and len(cards) == 2
    # the written text goes onto the rows of the run that saved the drafts, not the writing run
    _, card_run_id, copies = deps.recommendation_store.attached[0]
    assert card_run_id == SOURCE_RUN_ID
    assert {key: copy.summary for key, copy in copies.items()} == {
        card.stable_key: card.card_copy.summary for card in cards
    }


async def test_stage_degrades_with_a_notice_when_the_llm_is_down() -> None:
    deps = deps_for(
        FakeLLMClient({TaskId.card_write: LLMError(LLMErrorKind.overloaded, "busy")}), [make_draft()]
    )
    result = await WriteCardsStage().run(context(deps, Reporter()))
    assert result.status == "degraded"
    assert result.artifact is not None and result.artifact.template_cards == 1
    assert result.notices[0].code is RunNoticeCode.llm_fallback


async def test_fingerprint_tracks_prompt_versions() -> None:
    deps = deps_for(FakeLLMClient(), [])
    inputs = WriteCardsStage().fingerprint_inputs(context(deps, Reporter()))
    assert inputs["card_write"].startswith("v1+") and inputs["llm_enabled"] is True
