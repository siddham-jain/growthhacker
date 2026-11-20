"""golden scenarios replayed with frozen multilingual minilm vectors and a scripted adjudicator."""

import pytest

from app.domain.common import DirectnessLevel
from app.domain.topics import MatchMethod, ReviewReason
from tests.topics.golden import apollo, runable
from tests.topics.golden.scenario import ScenarioResult, run_scenario


@pytest.fixture(scope="module")
async def apollo_run(frozen_embedder) -> ScenarioResult:
    return await run_scenario(apollo, frozen_embedder)


@pytest.fixture(scope="module")
async def runable_run(frozen_embedder) -> ScenarioResult:
    return await run_scenario(runable, frozen_embedder)


@pytest.mark.parametrize("row", apollo.VIDEOS, ids=[row[0] for row in apollo.VIDEOS])
def test_apollo_matches(apollo_run: ScenarioResult, row) -> None:
    key, *_, expected = row
    assert apollo_run.accepted_labels(key, apollo) == expected


@pytest.mark.parametrize("row", runable.VIDEOS, ids=[row[0] for row in runable.VIDEOS])
def test_runable_matches(runable_run: ScenarioResult, row) -> None:
    key, *_, expected = row
    assert runable_run.accepted_labels(key, runable) == expected


def matches_for(result: ScenarioResult, key: str, fixture) -> dict[str, object]:
    labels = {ref.id: ref.label for ref in result.universe.refs}
    video = result.video(key, fixture)
    return {
        labels[match.topic_ref_id]: match
        for match in result.output.matches
        if match.video_id == video.video_id
    }


def test_english_and_hindi_titles_match_lexically_with_high_confidence(apollo_run) -> None:
    for key in ("en_knee_cost", "hi_knee_cost"):
        knee = matches_for(apollo_run, key, apollo)["Knee Replacement"]
        assert (knee.method, knee.confidence) == (MatchMethod.lexical_semantic, "high")
        assert knee.is_primary and knee.rank == 1


def test_unconfirmed_hinglish_aliases_go_through_the_quote_check(apollo_run) -> None:
    knee = matches_for(apollo_run, "hinglish_knee", apollo)["Knee Replacement"]
    assert knee.method is MatchMethod.llm_adjudicated
    assert {ReviewReason.proposed_alias_only, ReviewReason.llm_decided} <= set(knee.review_reasons)
    assert knee.evidence[0].alias_text == "ghutne ka operation"


def test_moon_landing_and_footer_lists_never_match(apollo_run) -> None:
    for key in ("negative_moon", "boilerplate_only", "tags_only", "clickbait"):
        assert all(match.decision != "accepted" for match in matches_for(apollo_run, key, apollo).values())


def test_broader_concepts_are_rejected_as_aliases(apollo_run, runable_run) -> None:
    knee = next(ref for ref in apollo_run.universe.refs if ref.label == "Knee Replacement")
    assert {alias.text: alias.status for alias in knee.aliases}["arthritis"] == "rejected"
    slides = next(ref for ref in runable_run.universe.refs if ref.label == "AI presentation maker")
    statuses = {alias.text: alias.status for alias in slides.aliases}
    assert statuses["pizza recipe"] == "rejected" and statuses["AI slide generator"] == "proposed"


def directness(result: ScenarioResult, key: str, fixture, question: str):
    labels = {ref.id: ref.label for ref in result.universe.refs}
    video = result.video(key, fixture)
    return next(
        row
        for row in result.output.directness
        if row.video_id == video.video_id and labels[row.buyer_question_ref_id] == question
    )


def test_answer_directness_levels(apollo_run, runable_run) -> None:
    question = "How much does knee replacement cost?"
    assert directness(apollo_run, "hi_knee_cost", apollo, question).score == pytest.approx(0.80)
    assert directness(apollo_run, "en_knee_cost", apollo, question).level is DirectnessLevel.direct
    assert directness(apollo_run, "hinglish_knee", apollo, question).level in {
        DirectnessLevel.mention,
        DirectnessLevel.none,
    }
    bypass = directness(apollo_run, "en_bypass", apollo, "How long is recovery after bypass surgery?")
    assert bypass.level is DirectnessLevel.direct
    slides = directness(runable_run, "en_slides", runable, "How much does an AI presentation maker cost?")
    assert slides.level in {DirectnessLevel.direct, DirectnessLevel.partial}
    assert slides.components.specificity == 1.0


def test_brand_is_masked_and_never_a_topic(runable_run) -> None:
    assert all(
        match.decision != "accepted" for match in matches_for(runable_run, "brand_only", runable).values()
    )
    assert "Runable" in runable_run.built.non_topical_terms


def test_negated_mentions_are_flagged_for_review(runable_run) -> None:
    agent = matches_for(runable_run, "negated", runable)["AI agent"]
    assert agent.evidence[0].negated
    assert ReviewReason.negated_evidence in agent.review_reasons


def test_explanations_name_the_evidence(apollo_run) -> None:
    knee = matches_for(apollo_run, "hi_knee_cost", apollo)["Knee Replacement"]
    assert knee.explanation.startswith("Matched to your offering 'Knee Replacement': the title contains")
    assert "based on title, description and tags only" in knee.explanation


def test_adjudication_stays_small(apollo_run, runable_run) -> None:
    assert apollo_run.output.adjudication.calls == 1
    assert runable_run.output.adjudication.calls == 1
