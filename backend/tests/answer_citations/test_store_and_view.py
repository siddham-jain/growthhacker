import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import insert, select, update

import app.api.services_impl  # noqa: F401  (registers the api services)
from app.api import ids
from app.api.services import api_service_registry
from app.api.services_impl.answer_citations import ANSWER_CITATIONS
from app.core.clock import FrozenClock
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.answer_citations import AnswerCitation, AnswerCitationCheck
from app.models.competitors import CompetitorSet
from app.models.tenancy import Client
from app.models.website import BuyerQuestion
from app.retention.rules import retention_registry
from app.services.answer_citations.models import CitationCheck, ResolvedCitation
from app.services.answer_citations.source import AnswerCitationSource
from app.services.answer_citations.store import AnswerCitationStore
from app.services.answer_citations.task import AnswerCitationRunner, WeeklyAnswerCitationTask
from app.workers.tasks import ANSWER_CITATIONS as TASK_NAME
from app.workers.tasks import maintenance_task_registry
from tests.answer_citations.fakes import OTHER, OWN, RIVAL, RIVAL_TWO, FakeAnswerSearch, result
from tests.conftest import make_settings
from tests.foundation.factories import create_tenant
from tests.youtube.fixtures import NOW, FakeGateway, video_record

pytestmark = pytest.mark.db

WEEK = timedelta(days=7)


async def add_question(
    session_factory: SessionFactory,
    scope: TenantScope,
    text: str,
    *,
    priority: int = 2,
    status: str = "confirmed",
) -> uuid.UUID:
    async with session_factory() as session:
        question_id = (
            await session.execute(
                insert(BuyerQuestion)
                .values(
                    workspace_id=scope.workspace_id,
                    client_id=scope.client_id,
                    text=text,
                    normalized_text=text.casefold(),
                    language="en",
                    funnel_stage="consideration",
                    intent="cost",
                    source="user",
                    status=status,
                    priority=priority,
                )
                .returning(BuyerQuestion.id)
            )
        ).scalar_one()
        await session.commit()
    return question_id


@pytest.fixture
async def scope(session_factory: SessionFactory) -> TenantScope:
    async with session_factory() as session:
        created = await create_tenant(
            session,
            f"apollo-{uuid.uuid4().hex[:6]}",
            own_channel_id=OWN,
            own_channel_status="verified",
            country="IN",
            lifecycle="active",
        )
        session.add(
            CompetitorSet(
                workspace_id=created.workspace_id,
                client_id=created.client_id,
                version=1,
                competitor_channel_ids=[RIVAL, RIVAL_TWO],
                confirmed_by="user",
                confirmed_at=NOW - timedelta(days=30),
            )
        )
        await session.commit()
    return created


def check(question_id: uuid.UUID, at, *citations: tuple[str, str]) -> CitationCheck:
    return CitationCheck(
        question_id=question_id,
        checked_at=at,
        result_count=5,
        youtube_url_count=len(citations),
        unresolved_count=0,
        answer_returned=True,
        credits=1.0,
        from_cache=False,
        citations=[
            ResolvedCitation(
                video_id=video_id,
                channel_id=channel_id,
                cited_url=f"https://youtu.be/{video_id}",
                position=rank,
                cited_in="result",
            )
            for rank, (video_id, channel_id) in enumerate(citations, start=1)
        ],
    )


async def test_due_questions_put_priority_first_skip_recent_and_respect_the_weekly_cap(
    session_factory: SessionFactory, scope: TenantScope
) -> None:
    store = AnswerCitationStore(session_factory, scope)
    low = await add_question(session_factory, scope, "low priority", priority=3)
    top = await add_question(session_factory, scope, "top priority", priority=1)
    recent = await add_question(session_factory, scope, "checked yesterday", priority=1)
    stale = await add_question(session_factory, scope, "checked last month", priority=1)
    await add_question(session_factory, scope, "not confirmed", priority=1, status="proposed")
    await store.save([check(recent, NOW - timedelta(days=1)), check(stale, NOW - timedelta(days=20))])

    due = await store.due_questions(NOW, weekly_cap=10, min_gap=timedelta(days=6))
    assert [question.id for question in due] == [top, stale, low]

    capped = await store.due_questions(NOW, weekly_cap=2, min_gap=timedelta(days=6))
    assert [question.id for question in capped] == [top]


async def test_latest_keeps_the_newest_check_inside_the_window(
    session_factory: SessionFactory, scope: TenantScope
) -> None:
    store = AnswerCitationStore(session_factory, scope)
    question = await add_question(session_factory, scope, "knee cost")
    expired = await add_question(session_factory, scope, "old question")
    await store.save(
        [
            check(question, NOW - timedelta(days=8), ("rivalVideo1", RIVAL)),
            check(question, NOW - timedelta(days=1), ("ownVideo001", OWN), ("rivalVideo1", RIVAL)),
            check(expired, NOW - timedelta(days=40), ("rivalVideo1", RIVAL)),
        ]
    )

    [latest] = await store.latest(NOW - timedelta(days=28))

    assert latest.question_id == question and latest.checked_at == NOW - timedelta(days=1)
    assert [citation.video_id for citation in latest.citations] == ["ownVideo001", "rivalVideo1"]


async def test_runner_checks_due_questions_and_stores_citations(
    session_factory: SessionFactory, scope: TenantScope
) -> None:
    question = await add_question(session_factory, scope, "knee replacement cost")
    clock = FrozenClock(NOW)
    gateway = FakeGateway(clock, videos=[video_record("rivalVideo1", channel_id=RIVAL)])
    search = FakeAnswerSearch({"knee replacement cost": result("https://youtu.be/rivalVideo1")})
    runner = AnswerCitationRunner(
        AnswerCitationSource(search, gateway, clock, max_results=10),
        session_factory,
        clock,
        make_settings().answer_citations,
    )
    async with session_factory() as session:
        client = await session.get(Client, scope.client_id)

    first = await runner.run_client(client, credit_budget=10)
    again = await runner.run_client(client, credit_budget=10)

    assert (first.questions, first.citations, first.credits) == (1, 1, 1.0)
    assert again.questions == 0 and len(search.calls) == 1
    assert search.calls[0][2] == "india"
    async with session_factory() as session:
        stored = (await session.execute(select(AnswerCitation))).scalars().all()
    assert [(row.buyer_question_id, row.video_id, row.channel_id) for row in stored] == [
        (question, "rivalVideo1", RIVAL)
    ]


class StubContainer:
    def __init__(self, session_factory: SessionFactory, adapters: dict[str, Any], **settings: Any) -> None:
        self.settings = make_settings(**settings)
        self.clock = FrozenClock(NOW)
        self.session_factory = session_factory
        self._adapters = adapters

    def adapter(self, port_name: str) -> Any:
        if port_name not in self._adapters:
            raise LookupError(port_name)
        return self._adapters[port_name]


async def test_weekly_task_is_registered_and_shares_one_credit_cap(
    session_factory: SessionFactory, scope: TenantScope
) -> None:
    async with session_factory() as session:
        other_scope = await create_tenant(session, f"clove-{uuid.uuid4().hex[:6]}", lifecycle="active")
        await session.commit()
    for index in range(3):
        await add_question(session_factory, scope, f"apollo question {index}")
        await add_question(session_factory, other_scope, f"clove question {index}")
    search = FakeAnswerSearch()
    container = StubContainer(
        session_factory,
        {"answer_search": search, "youtube": FakeGateway(FrozenClock(NOW))},
        answer_citations={"weekly_max_credits_total": 4},
    )
    # only the two test clients are active inside this transaction's view of the cap
    async with session_factory() as session:
        await session.execute(
            update(Client)
            .where(Client.id.not_in([scope.client_id, other_scope.client_id]))
            .values(lifecycle="paused")
        )
        await session.commit()

    assert maintenance_task_registry.get(TASK_NAME) is WeeklyAnswerCitationTask
    summary = await WeeklyAnswerCitationTask(container).run()

    assert summary["questions"] == 4 and summary["credits"] == 4 and len(search.calls) == 4


async def test_weekly_task_skips_without_a_search_provider(session_factory: SessionFactory) -> None:
    summary = await WeeklyAnswerCitationTask(StubContainer(session_factory, {})).run()
    assert summary["skipped"] == "not_configured"
    disabled = StubContainer(session_factory, {}, answer_citations={"enabled": False})
    assert (await WeeklyAnswerCitationTask(disabled).run()) == {"skipped": "disabled"}


async def test_view_labels_owners_and_lists_gaps(session_factory: SessionFactory, scope: TenantScope) -> None:
    yours = await add_question(session_factory, scope, "cites you", priority=1)
    gap = await add_question(session_factory, scope, "cites a rival only", priority=2)
    open_question = await add_question(session_factory, scope, "cites nobody tracked", priority=3)
    await AnswerCitationStore(session_factory, scope).save(
        [
            check(yours, NOW - timedelta(days=2), ("rivalVideo1", RIVAL), ("ownVideo001", OWN)),
            check(gap, NOW - timedelta(days=2), ("rivalVideo2", RIVAL_TWO), ("rivalVideo1", RIVAL)),
            check(open_question, NOW - timedelta(days=1), ("otherVideo1", OTHER)),
        ]
    )
    container = StubContainer(session_factory, {})

    view = await api_service_registry.get(ANSWER_CITATIONS)(container).view(scope)

    assert view.lens == "conversion" and view.window_days == 28 and view.own_channel_id == OWN
    assert view.disclosure == "Calculated by GrowthHacker, not a YouTube metric"
    assert [(q.text, q.status) for q in view.questions] == [
        ("cites you", "cites_you"),
        ("cites a rival only", "competitor_only"),
        ("cites nobody tracked", "others_only"),
    ]
    assert [video.owner for video in view.questions[0].videos] == ["own", "competitor"]
    counts = view.counts
    assert (counts.questions_checked, counts.cite_you, counts.cite_competitors) == (3, 1, 2)
    assert (counts.competitor_only, counts.no_tracked_channel) == (1, 1)
    assert view.last_checked_at == NOW - timedelta(days=1)
    by_channel = {entry.channel_id: entry.buyer_question_ids for entry in view.competitors}
    assert by_channel == {
        RIVAL: [ids.encode(ids.BUYER_QUESTION, yours), ids.encode(ids.BUYER_QUESTION, gap)],
        RIVAL_TWO: [ids.encode(ids.BUYER_QUESTION, gap)],
    }


async def test_rows_follow_the_28_day_api_rule(session_factory: SessionFactory) -> None:
    for table in ("answer_citation_checks", "answer_citations"):
        rule = retention_registry.get(table)
        assert rule.policy_bound and rule.max_age(make_settings().policy) == timedelta(days=28)
    assert {AnswerCitationCheck.__tablename__, AnswerCitation.__tablename__} <= set(retention_registry.keys())
