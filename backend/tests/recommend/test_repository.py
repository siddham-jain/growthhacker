import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RecommendSettings
from app.core.db import UnitOfWork
from app.domain.card_copy import CardCopy
from app.domain.recommendations import FeedbackStatus
from app.domain.recommendations import Recommendation as RecommendationCard
from app.models.recommendations import (
    CreatorCollaboration,
    Recommendation,
    RecommendationNotice,
    RecommendationOutcome,
    RecommendationReference,
    RecommendationSuppression,
    WhiteSpaceNote,
)
from app.models.runs import PipelineRun
from app.models.tenancy import User
from app.models.website import BuyerQuestion as BuyerQuestionRow
from app.services.recommend.assembly import with_copy
from app.services.recommend.feedback import OutcomeTracker, feedback_state
from app.services.recommend.feedback_service import FeedbackService, UnknownRecommendationError
from app.services.recommend.inputs import FeedbackState
from app.services.recommend.repository import RecommendationRepository, SqlRecommendationStore
from app.services.recommend.service import RecommendationService
from tests.foundation.factories import create_tenant, insert_row
from tests.recommend.fixtures import AS_OF, GENERATED_AT, TOPICS, apollo_inputs, question_id, video_id

pytestmark = pytest.mark.db


@pytest.fixture
async def scope(session: AsyncSession):
    tenant = await create_tenant(session, f"apollo-{uuid.uuid4().hex[:6]}")
    for topic in TOPICS:
        if topic.key.startswith("bq_"):
            await insert_row(
                session,
                BuyerQuestionRow.__table__,
                tenant,
                {
                    "id": question_id(topic.key),
                    "text": topic.label,
                    "normalized_text": topic.label.lower(),
                    "language": "en",
                },
            )
    return tenant


@pytest.fixture
async def run_row(session: AsyncSession, scope):
    return (await insert_row(session, PipelineRun.__table__, scope))["id"]


@pytest.fixture
async def user_id(session: AsyncSession, scope):
    row = await insert_row(
        session, User.__table__, scope, {"email": f"{uuid.uuid4().hex[:8]}@apollo.example"}
    )
    return row["id"]


async def generate(inputs=None):
    service = RecommendationService(RecommendSettings())
    return await service.generate(inputs or apollo_inputs(), run_id=uuid.uuid4(), generated_at=GENERATED_AT)


async def count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_a_run_persists_cards_references_suppressions_notices_and_white_space(
    session, scope, run_row
) -> None:
    result = await generate()
    repository = RecommendationRepository(session, scope)
    await repository.save_run(run_row, result)

    rows = (
        (await session.execute(select(Recommendation).where(Recommendation.run_id == run_row)))
        .scalars()
        .all()
    )
    assert len(rows) == len(result.cards)
    ranked = sorted((row for row in rows if row.window_days == 30 and row.rank), key=lambda row: row.rank)
    assert [row.rec_id for row in ranked] == [
        card.id for card in result.output.window(30).set.recommendations
    ]
    assert all(row.payload["extras"]["stable_key"] == row.stable_key for row in rows)
    assert await count(session, RecommendationReference) >= 3 * len(rows)
    assert await count(session, RecommendationSuppression) == len(result.suppressed)
    assert await count(session, RecommendationNotice) == sum(
        len(window.set.notices) for window in result.output.windows
    )
    assert await count(session, WhiteSpaceNote) == 3


async def test_previous_cards_carry_first_seen_and_evidence_hash_into_the_next_run(
    session, scope, run_row
) -> None:
    first = await generate()
    repository = RecommendationRepository(session, scope)
    await repository.save_run(run_row, first)

    previous = await repository.previous_cards(30)
    second = await generate(apollo_inputs(previous_cards=previous))

    statuses = {card.extras.status for card in second.output.window(30).set.recommendations}
    assert statuses == {"ongoing"}


async def test_feedback_is_keyed_by_stable_key_and_drives_config_effects(
    session, scope, run_row, user_id
) -> None:
    result = await generate()
    repository = RecommendationRepository(session, scope)
    await repository.save_run(run_row, result)
    knee = result.output.window(30).set.recommendations[0]
    service = FeedbackService(repository, RecommendSettings().feedback)

    effects = await service.record(
        rec_id=knee.id,
        user_id=user_id,
        status=FeedbackStatus.dismissed,
        reason="cannot_produce_format",
        now=AS_OF,
    )
    assert effects.config.excluded_formats == ["long_form|3–8m"]
    assert (await repository.config()).excluded_formats == ["long_form|3–8m"]
    assert len((await repository.config()).feedback_penalties) == 1

    await service.record(
        rec_id=knee.id, user_id=user_id, status=FeedbackStatus.snoozed, reason=None, now=AS_OF
    )
    rows = await repository.feedback_rows()
    assert [(row.stable_key, row.status) for row in rows] == [
        (knee.extras.stable_key, FeedbackStatus.snoozed)
    ]
    assert knee.extras.stable_key in feedback_state(rows, AS_OF, RecommendSettings().feedback).hidden_keys

    with pytest.raises(UnknownRecommendationError):
        await service.record(
            rec_id="rec_aaaaaaaaaaaa", user_id=user_id, status=FeedbackStatus.saved, reason=None, now=AS_OF
        )
    with pytest.raises(ValueError, match="only recorded with a dismissal"):
        await service.record(
            rec_id=knee.id, user_id=user_id, status=FeedbackStatus.saved, reason="other", now=AS_OF
        )


async def test_store_saves_the_run_and_measures_made_videos(
    session_factory, session, scope, run_row, user_id
) -> None:
    result = await generate()
    knee = result.output.window(30).set.recommendations[0]
    store = SqlRecommendationStore(lambda: UnitOfWork(session_factory), OutcomeTracker(auto_done_days=30))
    assert await store.save_run(scope, run_row, result, result.own_videos) == 0
    async with UnitOfWork(session_factory) as uow:
        await FeedbackService(
            RecommendationRepository(uow.session, scope), RecommendSettings().feedback
        ).record(
            rec_id=knee.id,
            user_id=user_id,
            status=FeedbackStatus.done,
            reason=None,
            now=AS_OF,
            linked_video_id=video_id("o2"),
        )
        await uow.commit()

    next_run = (await insert_row(session, PipelineRun.__table__, scope, {"status": "succeeded"}))["id"]
    measured = await store.save_run(scope, next_run, await generate(), result.own_videos)

    outcomes = (await session.execute(select(RecommendationOutcome))).scalars().all()
    assert measured == len(outcomes) >= 1
    linked = next(row for row in outcomes if row.video_id == video_id("o2"))
    assert linked.matched_by == "user" and linked.observed_ratio == pytest.approx(1.6)


async def test_cards_missing_from_the_next_run_expire_and_collaborations_are_shared(
    session, scope, run_row
) -> None:
    first = await generate()
    repository = RecommendationRepository(session, scope)
    await repository.save_run(run_row, first)
    dropped = first.output.window(30).set.recommendations[0]
    next_run = (await insert_row(session, PipelineRun.__table__, scope, {"status": "succeeded"}))["id"]
    second = await generate(
        apollo_inputs(feedback=FeedbackState(hidden_keys={dropped.extras.stable_key: "dismissed"}))
    )
    await repository.save_run(next_run, second)

    statuses = (
        (
            await session.execute(
                select(Recommendation.status).where(
                    Recommendation.run_id == run_row, Recommendation.rec_id == dropped.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert statuses == ["expired"]
    kinds = set((await session.execute(select(CreatorCollaboration.kind))).scalars().all())
    assert {"sponsor_language", "disclosed_paid_promotion"} <= kinds


def written(card: RecommendationCard, source: str, summary: str) -> CardCopy:
    structured = card.extras.why_it_works_structured
    return CardCopy(
        draft_id=f"d_{card.extras.stable_key[:16]}",
        summary=summary,
        why_it_works=[reason.statement for reason in structured[:1]],
        fit_to_client="Fits the knee replacement service you already offer",
        reason_fact_ids=[[reason.reason_id] for reason in structured[:1]],
        fit_fact_ids=[card.extras.fit_structured[0].reason_id],
        copy_source=source,  # type: ignore[arg-type]
        prompt_id="card_write",
        prompt_version="1",
        attempts=2,
        language="en",
    )


async def stored_card(session: AsyncSession, run_id: uuid.UUID, stable_key: str) -> Recommendation:
    statement = select(Recommendation).where(
        Recommendation.run_id == run_id, Recommendation.stable_key == stable_key
    )
    return (await session.execute(statement.execution_options(populate_existing=True))).scalar_one()


async def test_llm_copy_lands_on_the_card_row_and_template_never_replaces_it(session, scope, run_row) -> None:
    result = await generate()
    repository = RecommendationRepository(session, scope)
    await repository.save_run(run_row, result)
    card = result.output.window(30).set.recommendations[0]
    key = card.extras.stable_key
    rows_before = await count(session, Recommendation)

    llm = written(card, "llm_revised", "Film a knee recovery explainer answering the top patient question")
    assert await repository.attach_copy(run_row, {key: llm}) == 1
    row = await stored_card(session, run_row, key)
    stored = RecommendationCard.model_validate(row.payload)
    assert stored.summary == llm.summary and stored.why_it_works == llm.why_it_works
    assert stored.extras.rendered_by == "llm" and row.writer_meta["rendered_by"] == "llm"
    assert row.writer_meta["evidence_hash"] and row.writer_meta["attempts"] == 2
    # numbers and references stay as recommend assembled them
    assert stored.reference_videos == card.reference_videos
    assert stored.expected_range_vs_baseline == card.expected_range_vs_baseline
    assert stored.confidence == card.confidence and stored.extras.subject == card.extras.subject

    template = written(card, "template", "Knee replacement: a long-form video")
    assert await repository.attach_copy(run_row, {key: template}) == 0
    assert await repository.attach_copy(run_row, {key: llm}) == 1
    row = await stored_card(session, run_row, key)
    assert row.payload["summary"] == llm.summary
    assert await count(session, Recommendation) == rows_before


async def test_template_copy_never_rewords_an_llm_card() -> None:
    card = (await generate()).output.window(30).set.recommendations[0]
    card = card.model_copy(update={"extras": card.extras.model_copy(update={"rendered_by": "llm"})})
    template = written(card, "template", "Knee replacement: a long-form video")
    assert with_copy(card, template) is card
    llm = written(card, "llm", "Film a knee recovery explainer this week")
    assert with_copy(card, llm).summary == llm.summary
