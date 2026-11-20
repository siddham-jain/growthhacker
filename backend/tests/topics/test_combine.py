import pytest

from app.core.config import TopicEngineSettings
from app.domain.topics import MatchMethod, ReviewReason
from app.services.topics.matching.base import MatcherResult
from app.services.topics.matching.combine import (
    DecisionPolicy,
    LogisticCombiner,
    NoisyOrCombiner,
    build_combiner,
)
from tests.topics.fixtures import APOLLO_CLIENT, offering, prepare, profile, video_text
from tests.topics.helpers import universe_for

SETTINGS = TopicEngineSettings()


@pytest.fixture
async def ref():
    built = await universe_for(
        profile(APOLLO_CLIENT, name="Apollo", offerings=[offering("o", "Knee Replacement")])
    )
    return built.universe.refs[0]


def lexical(score: float, *flags: str) -> MatcherResult:
    return MatcherResult(score=score, flags=frozenset({"title_hit", "clean_headline_hit", *flags}))


def semantic(score: float, margin: float, band: str = "strong") -> MatcherResult:
    return MatcherResult(score=score, detail={"margin": margin, "band": band})


def evaluate(
    ref,
    lexical_result=None,
    semantic_result=None,
    missing=None,
    title="Knee Replacement explained",
    settings=SETTINGS,
):
    video = prepare(video_text(title))
    return DecisionPolicy(settings, NoisyOrCombiner(settings)).evaluate(
        video, ref, lexical_result, semantic_result, missing
    )


def test_noisy_or_combination() -> None:
    combiner = NoisyOrCombiner(SETTINGS)
    assert combiner.combine(1.0, 1.0, {}) == pytest.approx(1 - 0.1 * 0.25)
    assert combiner.combine(0.5, None, {}) == pytest.approx(0.45)


def test_logistic_combiner_is_used_once_fitted() -> None:
    fitted = TopicEngineSettings(logistic_coefficients=[-4.0, 6.0, 3.0, 0.0, 1.0, -1.0, -1.0])
    combiner = build_combiner(fitted)
    assert isinstance(combiner, LogisticCombiner)
    assert combiner.combine(1.0, 1.0, {"title_hit": 1.0, "tags_only": 0.0, "quality_low": 0.0}) > 0.99
    with pytest.raises(ValueError):
        LogisticCombiner(TopicEngineSettings(logistic_coefficients=[1.0]))


async def test_bands_and_high_confidence(ref) -> None:
    strong = evaluate(ref, lexical(1.0), semantic(0.8, 0.4))
    assert (strong.band, strong.decision, strong.confidence) == ("accept", "accepted", "high")
    assert strong.method is MatchMethod.lexical_semantic
    lexical_only = evaluate(ref, lexical(1.0), semantic(0.0, 0.05, "weak"))
    assert (lexical_only.decision, lexical_only.confidence, lexical_only.method) == (
        "accepted",
        "medium",
        MatchMethod.lexical,
    )
    weak = evaluate(ref, lexical(0.2), semantic(0.1, 0.05, "weak"))
    assert (weak.band, weak.decision, weak.review) == ("reject", "rejected", [])
    middle = evaluate(ref, lexical(0.5), semantic(0.1, 0.05, "weak"))
    assert (middle.band, middle.decision) == ("ambiguous", "pending")


@pytest.mark.parametrize(
    ("lexical_result", "semantic_result", "reason"),
    [
        (None, semantic(0.85, 0.42), ReviewReason.semantic_only),
        (lexical(1.0, "ambiguous_only"), semantic(0.8, 0.4), ReviewReason.ambiguous_alias),
        (lexical(1.0, "all_negated"), semantic(0.8, 0.4), ReviewReason.negated_evidence),
        (lexical(1.0, "tags_only"), semantic(0.8, 0.4), ReviewReason.tags_only),
        (lexical(1.0), semantic(0.0, -0.05, "weak"), ReviewReason.method_conflict),
        (lexical(1.0, "proposed_only"), semantic(0.1, 0.05, "weak"), ReviewReason.proposed_alias_only),
    ],
)
async def test_forced_adjudication(ref, lexical_result, semantic_result, reason) -> None:
    state = evaluate(ref, lexical_result, semantic_result)
    assert reason in state.forced
    assert state.decision == "pending"


async def test_cross_lingual_hits_are_not_method_conflicts(ref) -> None:
    state = evaluate(ref, lexical(1.0, "cross_lingual_only"), semantic(0.0, -0.05, "weak"))
    assert ReviewReason.method_conflict not in state.forced
    assert state.decision == "accepted"


async def test_semantic_only_above_point_nine_is_accepted_at_medium(ref) -> None:
    state = evaluate(ref, None, semantic(1.0, 0.6))
    assert (state.decision, state.confidence, state.method) == ("accepted", "medium", MatchMethod.semantic)


async def test_no_embedder_needs_strong_lexical_and_caps_confidence(ref) -> None:
    strong = evaluate(ref, lexical(1.0), None, missing="no_embedder")
    assert (strong.decision, strong.confidence, strong.semantic_score) == ("accepted", "medium", None)
    assert "no_embedder" in strong.degraded and ReviewReason.degraded_mode in strong.review
    weaker = evaluate(ref, lexical(0.79), None, missing="no_embedder")
    assert weaker.decision == "pending"


async def test_without_llm_fallbacks(ref) -> None:
    policy = DecisionPolicy(SETTINGS, NoisyOrCombiner(SETTINGS))
    forced_high = evaluate(ref, lexical(1.0, "tags_only"), semantic(0.8, 0.4))
    policy.without_llm(forced_high, "no_llm")
    assert (forced_high.decision, forced_high.confidence) == ("accepted", "low")
    conflict = evaluate(ref, lexical(1.0), semantic(0.0, -0.05, "weak"))
    policy.without_llm(conflict, "no_llm")
    assert conflict.decision == "unadjudicated"
    budget = evaluate(ref, lexical(0.5), semantic(0.1, 0.05, "weak"))
    policy.without_llm(budget, "llm_budget")
    assert budget.decision == "unadjudicated"
    assert ReviewReason.unadjudicated_budget in budget.review and "llm_budget" in budget.degraded


async def test_llm_verdict_mapping(ref) -> None:
    policy = DecisionPolicy(SETTINGS, NoisyOrCombiner(SETTINGS))
    state = evaluate(ref, lexical(0.5), semantic(0.1, 0.05, "weak"))
    policy.llm_accepted(state, "Knee Replacement", "title", {"model": "m", "prompt_version": "v"})
    assert (state.method, state.confidence, state.combined) == (MatchMethod.llm_adjudicated, "medium", 0.7)
    assert ReviewReason.llm_decided in state.review
    tags = evaluate(ref, lexical(0.5), semantic(0.1, 0.05, "weak"))
    policy.llm_accepted(tags, "knee", "tags", {})
    assert tags.confidence == "low"
    unverified = evaluate(ref, lexical(0.5), semantic(0.1, 0.05, "weak"))
    policy.llm_rejected(unverified, {}, unverified_quote="invented words")
    assert unverified.decision == "rejected" and ReviewReason.llm_quote_unverified in unverified.review
