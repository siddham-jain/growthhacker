import uuid

from app.adapters.search.errors import WebSearchError
from app.core.clock import FrozenClock
from app.domain.youtube import QuotaPriority
from app.services.answer_citations.models import QuestionRef
from app.services.answer_citations.source import AnswerCitationSource
from app.services.youtube.context import request_context
from tests.answer_citations.fakes import OWN, RIVAL, FakeAnswerSearch, result
from tests.youtube.fixtures import NOW, FakeGateway, video_record

CLIENT_ID = uuid.uuid4()
CTX = request_context(QuotaPriority.background, client_id=CLIENT_ID)


def question(text: str) -> QuestionRef:
    return QuestionRef(id=uuid.uuid4(), text=text)


def source(search: FakeAnswerSearch, gateway: FakeGateway) -> AnswerCitationSource:
    return AnswerCitationSource(search, gateway, FrozenClock(NOW), max_results=10)


async def test_resolves_cited_videos_in_one_batch_and_counts_unresolved() -> None:
    clock = FrozenClock(NOW)
    gateway = FakeGateway(
        clock,
        videos=[
            video_record("ownVideo001", channel_id=OWN),
            video_record("rivalVideo1", channel_id=RIVAL),
            video_record("privateVid1", channel_id=RIVAL, privacy="private"),
        ],
    )
    search = FakeAnswerSearch(
        {
            "knee replacement cost": result(
                "https://www.youtube.com/watch?v=ownVideo001",
                "https://example.com/cost",
                "https://youtu.be/missingVid1",
            ),
            "knee recovery time": result(
                "https://www.youtube.com/watch?v=rivalVideo1",
                "https://youtu.be/privateVid1",
                answer="See https://youtu.be/ownVideo001.",
            ),
        }
    )
    cost, recovery = question("knee replacement cost"), question("knee recovery time")

    checks = await source(search, gateway).check([cost, recovery], CTX, country="IN", credit_budget=10)

    assert gateway.calls == ["videos.list"]
    assert [call[2:] for call in search.calls] == [("IN", CLIENT_ID), ("IN", CLIENT_ID)]
    first, second = checks
    assert first.question_id == cost.id and first.checked_at == NOW
    assert [(c.video_id, c.channel_id, c.position) for c in first.citations] == [("ownVideo001", OWN, 1)]
    assert (first.result_count, first.youtube_url_count, first.unresolved_count) == (3, 2, 1)
    assert [(c.video_id, c.cited_in) for c in second.citations] == [
        ("rivalVideo1", "result"),
        ("ownVideo001", "answer"),
    ]
    assert second.unresolved_count == 1
    assert first.credits == 1.0 and not first.from_cache


async def test_stops_before_the_credit_budget_is_crossed() -> None:
    search = FakeAnswerSearch()
    questions = [question(f"q{index}") for index in range(5)]

    checks = await source(search, FakeGateway(FrozenClock(NOW))).check(
        questions, CTX, country=None, credit_budget=2
    )

    assert len(search.calls) == 2
    assert [check.question_id for check in checks] == [questions[0].id, questions[1].id]


async def test_cached_answers_spend_nothing_from_the_budget() -> None:
    search = FakeAnswerSearch({f"q{index}": result(from_cache=True) for index in range(3)})

    checks = await source(search, FakeGateway(FrozenClock(NOW))).check(
        [question(f"q{index}") for index in range(3)], CTX, country=None, credit_budget=1
    )

    assert len(checks) == 3 and all(check.from_cache and check.credits == 0 for check in checks)


async def test_a_failed_search_is_skipped_and_a_credit_limit_stops_the_rest() -> None:
    search = FakeAnswerSearch(
        {"q1": WebSearchError("timeout", "slow"), "q2": WebSearchError("credit_limit", "out of credits")}
    )
    questions = [question(text) for text in ("q0", "q1", "q2", "q3")]

    checks = await source(search, FakeGateway(FrozenClock(NOW))).check(
        questions, CTX, country=None, credit_budget=10
    )

    assert [call[0] for call in search.calls] == ["q0", "q1", "q2"]
    assert [check.question_id for check in checks] == [questions[0].id]


async def test_no_youtube_citations_makes_no_api_call() -> None:
    gateway = FakeGateway(FrozenClock(NOW))
    search = FakeAnswerSearch({"q": result("https://example.com/a", answer=None)})

    [check] = await source(search, gateway).check([question("q")], CTX, country=None, credit_budget=1)

    assert gateway.calls == []
    assert check.citations == [] and not check.answer_returned and check.youtube_url_count == 0
