import pytest

from app.core.config import TopicEngineSettings
from app.domain.common import DirectnessLevel
from app.domain.topics import MatchEvidence
from app.services.topics.directness.scorer import (
    AnswerDirectnessScorer,
    DirectnessInput,
    level_for,
    nearest_boundary,
)
from app.services.topics.directness.tiebreak import DirectnessTieBreaker, tiebreak_cases
from app.services.topics.lexicon import load_lexicons
from app.services.topics.llm import UnlimitedSpend
from tests.topics.fixtures import APOLLO_CLIENT, FakeLLM, prepare, video_text
from tests.topics.golden import apollo
from tests.topics.helpers import universe_for


@pytest.fixture
async def question():
    built = await universe_for(apollo.PROFILE, apollo.QUESTIONS)
    return next(ref for ref in built.universe.refs if ref.label == "How much does knee replacement cost?")


def entity(field: str = "title", quality: float = 1.0, **detail) -> MatchEvidence:
    return MatchEvidence(
        method="lexical", field=field, match_quality=quality, alias_text="knee replacement", detail=detail
    )


def score(question, title, description="", tags=(), evidence=None, **kwargs):
    data = DirectnessInput(
        client_id=APOLLO_CLIENT,
        universe_hash="u",
        video=prepare(video_text(title, description, tags)),
        question=question,
        parent_evidence=[entity()] if evidence is None else evidence,
        **kwargs,
    )
    return AnswerDirectnessScorer(load_lexicons()).score(data)


def test_levels_and_boundaries() -> None:
    assert [level_for(value) for value in (0.7, 0.45, 0.2, 0.19)] == [
        DirectnessLevel.direct,
        DirectnessLevel.partial,
        DirectnessLevel.mention,
        DirectnessLevel.none,
    ]
    assert nearest_boundary(0.68, 0.05) == 0.70
    assert nearest_boundary(0.58, 0.05) is None


async def test_worked_example_hindi_question_title_is_direct(question) -> None:
    result = score(question, "घुटने का प्रत्यारोपण कितने का होता है? | Knee Replacement Cost in India")
    components = result.components
    assert (components.question_alignment, components.facet_coverage, components.entity_coverage) == (
        1.0,
        1.0,
        1.0,
    )
    assert (components.specificity, components.intent_cues, components.penalties) == (0.0, 0.0, 0.0)
    assert result.score == pytest.approx(0.80)
    assert result.level is DirectnessLevel.direct
    assert result.facets_covered == [question.facets[0]]
    assert result.explanation.startswith("Directly answers 'How much does knee replacement cost?'")
    assert result.basis == "metadata_only"


async def test_worked_example_promotional_event_is_none(question) -> None:
    result = score(
        question, "Apollo celebrates 1,000 robotic knee surgeries", evidence=[entity(quality=0.85)]
    )
    assert result.components.entity_coverage == pytest.approx(0.85)
    assert result.components.facet_coverage == 0.0
    assert result.components.penalties == pytest.approx(0.20)
    assert result.score < 0.20
    assert result.level is DirectnessLevel.none
    assert "promotional" in result.explanation


async def test_price_amounts_raise_specificity(question) -> None:
    plain = score(question, "Knee replacement cost in India")
    priced = score(question, "Knee replacement cost in India: ₹2.5 lakh package")
    assert priced.components.specificity == 1.0
    assert priced.score == pytest.approx(plain.score + 0.10, abs=1e-3)


async def test_shorts_are_capped_at_partial_unless_the_title_asks_the_question(question) -> None:
    short = score(question, "Knee replacement cost explained, all inclusive ₹ 2 lakh", is_short=True)
    assert short.level is DirectnessLevel.partial
    asking = score(question, "How much does knee replacement cost?", is_short=True)
    assert asking.level is DirectnessLevel.direct


async def test_tags_only_and_list_mentions_are_penalised(question) -> None:
    tags_only = score(question, "Our clinic tour", tags=["knee replacement cost"], evidence=[entity("tags")])
    assert tags_only.components.penalties == pytest.approx(0.10)
    listed = score(question, "Knee replacement cost", refs_matched_in_title=4)
    assert listed.components.penalties == pytest.approx(0.15)


async def test_semantic_title_similarity_can_carry_alignment(question) -> None:
    result = score(question, "घुटना बदलवाने में कितना पैसा लगता है", evidence=[], title_semantic=0.9)
    assert result.components.question_alignment == pytest.approx(0.9)


async def test_tiebreak_moves_a_borderline_score_by_one_level(question) -> None:
    row = score(question, "Knee replacement cost in India, explained")
    borderline = row.model_copy(update={"score": 0.68, "level": DirectnessLevel.partial})
    video = prepare(video_text("Knee replacement cost in India, explained"))
    cases = tiebreak_cases([borderline], {row.video_id: video}, {question.id: question}, 0.05)
    assert [case.options for case in cases] == [(DirectnessLevel.partial, DirectnessLevel.direct)]
    llm = FakeLLM(tiebreak=lambda video: ("direct", "Knee replacement cost in India"))
    resolved = await DirectnessTieBreaker(llm, TopicEngineSettings(), UnlimitedSpend()).resolve(
        cases, client_id=APOLLO_CLIENT, run_id=None
    )
    assert resolved[0].score == pytest.approx(0.71)
    assert resolved[0].method == "deterministic+llm_tiebreak"
    assert "adjudicated: direct" in resolved[0].explanation


async def test_tiebreak_ignores_far_levels_and_unverified_quotes(question) -> None:
    row = score(question, "Knee replacement cost in India, explained").model_copy(update={"score": 0.68})
    video = prepare(video_text("Knee replacement cost in India, explained"))
    cases = tiebreak_cases([row], {row.video_id: video}, {question.id: question}, 0.05)
    breaker = DirectnessTieBreaker(
        FakeLLM(tiebreak=lambda v: ("none", "Knee replacement")), TopicEngineSettings(), UnlimitedSpend()
    )
    assert await breaker.resolve(cases, client_id=APOLLO_CLIENT, run_id=None) == []
    breaker = DirectnessTieBreaker(
        FakeLLM(tiebreak=lambda v: ("direct", "free surgery today")), TopicEngineSettings(), UnlimitedSpend()
    )
    assert await breaker.resolve(cases, client_id=APOLLO_CLIENT, run_id=None) == []
