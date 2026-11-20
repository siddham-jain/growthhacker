import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.core.tenancy import TenantScope
from app.domain.card_copy import CardCopy
from app.domain.recommendations import Recommendation, RecommendationDraft
from app.domain.runs import RunKind, RunTrigger
from app.models.llm import RecommendationCopy
from app.models.recommendations import Recommendation as RecommendationRow
from app.models.runs import ActiveResultSet
from app.pipeline.artifacts import RunArtifactReader
from app.pipeline.contracts import Market, RunConfigSnapshot, RunContext
from app.pipeline.inputs.cards import TemplateCardInputs
from app.pipeline.stages.cards import WRITTEN_CARDS, WriteCardsStage
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.services.llm.budgets import MemoryBudgetLedger
from app.services.llm.writer.copy_store import RecommendationCopyStore
from app.services.llm.writer.schemas import WrittenCard, WrittenCards
from app.services.recommend.feedback import OutcomeTracker
from app.services.recommend.repository import SqlRecommendationStore
from tests.api.conftest import Harness
from tests.api_services.conftest import Tenant, artifacts, finished_run, seed_profile
from tests.api_services.test_results import analytics_result, published  # noqa: F401  (shared fixture)
from tests.competitors.apollo import apollo_profile
from tests.llm.test_cards_stage import Cancel, Reporter

pytestmark = pytest.mark.db


class Writing:
    """a stand-in for 10's writing service: validated llm copy for chosen cards, template for the rest."""

    def __init__(self, llm_keys: set[str]) -> None:
        self.llm_keys = llm_keys
        self.seen: list[str] = []

    def copy(self, draft: RecommendationDraft) -> CardCopy:
        llm = draft.stable_key in self.llm_keys
        reason, fit = draft.reasons[0], draft.fit_reasons[0]
        return CardCopy(
            draft_id=draft.draft_id,
            summary=f"Film the {draft.type.value} recovery explainer patients ask about" if llm else "Plain",
            why_it_works=[reason.statement],
            fit_to_client=fit.statement,
            reason_fact_ids=[[reason.reason_id]],
            fit_fact_ids=[fit.reason_id],
            copy_source="llm_revised" if llm else "template",
            degrade_reason=None if llm else "budget",
            prompt_id="card_write",
            prompt_version="1",
            attempts=2 if llm else 0,
            language="en",
        )

    async def write(
        self, drafts: list[RecommendationDraft], client: Any, *, on_card: Any, **_: Any
    ) -> WrittenCards:
        cards = []
        for draft in drafts:
            self.seen.append(draft.stable_key)
            card = WrittenCard(
                draft_id=draft.draft_id, stable_key=draft.stable_key, card_copy=self.copy(draft)
            )
            await on_card(card)
            cards.append(card)
        llm = sum(card.card_copy.copy_source != "template" for card in cards)
        return WrittenCards(cards=cards, revised_cards=llm, template_cards=len(cards) - llm)


@dataclass
class Deps:
    settings: Any
    card_inputs: TemplateCardInputs
    card_writing: Writing
    llm_budget: MemoryBudgetLedger
    recommendation_copy: RecommendationCopyStore
    recommendation_store: SqlRecommendationStore


async def write_cards(harness: Harness, scope: TenantScope, run_id: uuid.UUID, window: int, writing: Writing):
    container = harness.container
    deps = Deps(
        settings=harness.settings,
        card_inputs=TemplateCardInputs(container),
        card_writing=writing,
        llm_budget=MemoryBudgetLedger(harness.settings.llm.budgets),
        recommendation_copy=RecommendationCopyStore(container.session_factory),
        recommendation_store=SqlRecommendationStore(container.uow, OutcomeTracker(auto_done_days=30)),
    )
    snapshot = RunConfigSnapshot(
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        config_version=1,
        config_hash="h",
        window_days=window,
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    ctx = RunContext(
        run_id=run_id,
        kind=RunKind.cards,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=container.clock.now(),
        snapshot=snapshot,
        clock=container.clock,
        budget=None,  # type: ignore[arg-type]
        reporter=Reporter(),
        artifacts=RunArtifactReader(artifacts(harness, scope), run_id),
        cancel=Cancel(),
        deps=deps,
        stage="write_cards",
    )
    result = await WriteCardsStage().run(ctx)
    # the runner stores what the stage produced under the writing run
    assert result.artifact is not None
    await artifacts(harness, scope).put(run_id, WRITTEN_CARDS, result.artifact, fingerprint="written")
    return result


async def cards_run(harness: Harness, owner: Tenant, run: Any) -> uuid.UUID:
    # a cards run inherits recommend: the runner copies the source run's artifact into it
    run_id = await finished_run(harness, owner.scope)
    await artifacts(harness, owner.scope).put(run_id, RECOMMEND_ARTIFACT, run.output, fingerprint="inherited")
    return run_id


async def publish_window(harness: Harness, scope: TenantScope, run_id: uuid.UUID, window: int) -> None:
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            update(ActiveResultSet)
            .where(ActiveResultSet.client_id == scope.client_id, ActiveResultSet.window_days == window)
            .values(run_id=run_id)
        )


async def listed(harness: Harness, owner: Tenant) -> dict[str, dict[str, Any]]:
    response = await harness.client.get(f"{owner.path}/recommendations", headers=owner.headers)
    assert response.status_code == 200, response.text
    return {item["recommendation"]["id"]: item["recommendation"] for item in response.json()["data"]["items"]}


async def counts(harness: Harness, scope: TenantScope) -> tuple[int, int]:
    async with harness.container.session_factory() as session:
        cards = await session.scalar(
            select(func.count())
            .select_from(RecommendationRow)
            .where(RecommendationRow.client_id == scope.client_id)
        )
        copies = await session.scalar(
            select(func.count())
            .select_from(RecommendationCopy)
            .where(RecommendationCopy.client_id == scope.client_id)
        )
    return cards or 0, copies or 0


async def test_cards_run_copy_reaches_the_published_card_without_regressing_or_duplicating(
    harness: Harness,
    analytics_result,  # noqa: F811
) -> None:
    owner, source_run, run = await published(harness, "cards-copy", analytics_result)
    await seed_profile(harness, owner.scope, apollo_profile())
    # the api serves the client's default 30-day window
    window = run.output.window(30).set
    days = window.window_days
    first, second = window.recommendations[:2]
    assert first.extras.rendered_by == second.extras.rendered_by == "template"

    first_run = await cards_run(harness, owner, run)
    writing = Writing(llm_keys={first.extras.stable_key})
    result = await write_cards(harness, owner.scope, first_run, days, writing)
    # the inherited recommend output points the cards run at the source run's template cards
    assert {first.extras.stable_key, second.extras.stable_key} <= set(writing.seen)
    assert result.counts["revised_cards"] == 1
    await publish_window(harness, owner.scope, first_run, days)

    cards = await listed(harness, owner)
    drafts = {card.draft.stable_key: card.draft for card in run.cards}
    llm_summary = writing.copy(drafts[first.extras.stable_key]).summary
    assert cards[first.id]["summary"] == llm_summary and cards[first.id]["extras"]["rendered_by"] == "llm"
    assert cards[first.id]["why_it_works"] == [first.extras.why_it_works_structured[0].statement]
    assert cards[first.id]["reference_videos"] == first.model_dump(mode="json")["reference_videos"]
    assert cards[first.id]["expected_range_vs_baseline"] == first.expected_range_vs_baseline
    assert cards[second.id]["summary"] == "Plain"
    single = await harness.client.get(f"/api/v1/recommendations/{first.id}", headers=owner.headers)
    assert single.json()["recommendation"]["summary"] == llm_summary
    before = await counts(harness, owner.scope)

    # re-running the cards run and a later cards run that only gets template wording change nothing
    fallback = Writing(llm_keys=set())
    await write_cards(harness, owner.scope, first_run, days, fallback)
    second_run = await cards_run(harness, owner, run)
    await write_cards(harness, owner.scope, second_run, days, fallback)
    assert first.extras.stable_key not in fallback.seen and second.extras.stable_key in fallback.seen
    await publish_window(harness, owner.scope, second_run, days)

    cards = await listed(harness, owner)
    assert cards[first.id]["summary"] == llm_summary and cards[first.id]["extras"]["rendered_by"] == "llm"
    assert cards[second.id]["summary"] == "Plain"
    single = await harness.client.get(f"/api/v1/recommendations/{first.id}", headers=owner.headers)
    assert single.json()["recommendation"]["summary"] == llm_summary
    assert await counts(harness, owner.scope) == before
    async with harness.container.session_factory() as session:
        row = await session.scalar(
            select(RecommendationRow).where(
                RecommendationRow.run_id == source_run,
                RecommendationRow.stable_key == first.extras.stable_key,
            )
        )
    assert row is not None and Recommendation.model_validate(row.payload).summary == llm_summary
