import pytest

from app.core.config import TopicEngineSettings
from app.domain.topics import MatchEvidence, MatchMethod, ReviewReason, TopicMatch
from app.services.topics.calibration.fit import (
    expected_calibration_error,
    fit_platt,
    pair_metrics,
    pick_thresholds,
)
from app.services.topics.matching.assign import TopicAssigner
from app.services.topics.matching.base import MatcherResult
from app.services.topics.matching.combine import DecisionPolicy, NoisyOrCombiner
from app.services.topics.pipeline import blank_match, rerank
from app.services.topics.review.explain import MatchExplainer
from app.services.topics.review.flagger import ReviewFlagger, uncertainty
from app.services.topics.review.queue import OverrideRule, apply_rules
from tests.topics.fixtures import NOW, prepare, video_text
from tests.topics.golden import apollo
from tests.topics.helpers import universe_for

SETTINGS = TopicEngineSettings()


@pytest.fixture
async def universe():
    return (await universe_for(apollo.PROFILE, apollo.QUESTIONS)).universe


def refs(universe):
    return {ref.label: ref for ref in universe.refs}


def state(video, ref, lexical=1.0, semantic=0.8, *flags):
    policy = DecisionPolicy(SETTINGS, NoisyOrCombiner(SETTINGS))
    return policy.evaluate(
        video,
        ref,
        MatcherResult(
            score=lexical,
            flags=frozenset({"title_hit", "clean_headline_hit", *flags}),
            detail={"max_specificity": 1.0, "alias_tokens": 2},
        ),
        MatcherResult(score=semantic, detail={"margin": 0.4, "band": "strong"}),
        None,
    )


async def test_implied_parents_are_one_notch_lower(universe) -> None:
    by_label = refs(universe)
    video = prepare(video_text("Knee replacement cost in India"))
    child = state(video, by_label["How much does knee replacement cost?"])
    assert child.confidence == "high"
    result = TopicAssigner(SETTINGS).assign(video, [child], {ref.id: ref for ref in universe.refs})
    implied = next(pair for pair in result.pairs if pair.ref.label == "Knee Replacement")
    assert (implied.method, implied.confidence, implied.implied_from) == (
        MatchMethod.implied,
        "medium",
        child.ref.id,
    )
    assert result.unmatched_reason is None


async def test_caps_keep_the_top_five_and_flag_many_topics(universe) -> None:
    video = prepare(video_text("Omnibus episode covering many procedures"))
    pairs = [state(video, ref, lexical=0.9 - index * 0.05) for index, ref in enumerate(universe.refs[:7])]
    result = TopicAssigner(SETTINGS).assign(video, pairs, {ref.id: ref for ref in universe.refs})
    accepted = [pair for pair in result.pairs if pair.decision == "accepted"]
    assert len(accepted) == 5
    assert all(ReviewReason.many_topics in pair.review for pair in accepted)


async def test_subsumed_only_matches_are_dropped(universe) -> None:
    video = prepare(video_text("Robotic knee surgery"))
    pair = state(video, refs(universe)["IVF"], 1.0, 0.8, "subsumed_only")
    result = TopicAssigner(SETTINGS).assign(video, [pair], {ref.id: ref for ref in universe.refs})
    assert result.pairs[0].decision == "rejected"
    assert result.unmatched_reason == "below_threshold"


async def test_unmatched_reasons(universe) -> None:
    assigner = TopicAssigner(SETTINGS)
    index = {ref.id: ref for ref in universe.refs}
    assert (
        assigner.assign(prepare(video_text("Is this the end?")), [], index).unmatched_reason
        == "no_topical_text"
    )
    assert (
        assigner.assign(prepare(video_text("முழங்கால் வலி சிகிச்சை")), [], index).unmatched_reason
        == "unsupported_script"
    )
    video = prepare(video_text("Knee pain"))
    pending = state(video, refs(universe)["Knee Replacement"], 0.5, 0.0)
    pending.decision = "unadjudicated"
    assert assigner.assign(video, [pending], index).unmatched_reason == "unadjudicated"


def match(universe, label: str, video, score: float, decision="accepted") -> TopicMatch:
    ref = refs(universe)[label]
    return blank_match(ref, video, universe, "e", NOW).model_copy(
        update={
            "decision": decision,
            "combined_score": score,
            "method": MatchMethod.lexical,
            "confidence": "medium",
        }
    )


async def test_rerank_picks_entity_primary_with_deterministic_ties(universe) -> None:
    video = prepare(video_text("Knee replacement cost"))
    ranked = rerank(
        [
            match(universe, "How much does knee replacement cost?", video, 0.95),
            match(universe, "Knee Replacement", video, 0.9),
            match(universe, "IVF", video, 0.9),
            match(universe, "heart attack", video, 0.2, "rejected"),
        ],
        {ref.id: ref for ref in universe.refs},
    )
    assert [item.rank for item in ranked] == [1, 2, 3, 0]
    primary = [item for item in ranked if item.is_primary]
    assert len(primary) == 1 and primary[0].topic_kind.value == "offering"


async def test_overrides_accept_reject_and_reassign(universe) -> None:
    video = prepare(video_text("Knee replacement cost"))
    by_label = refs(universe)
    known = {ref.id: ref for ref in universe.refs}
    current = [match(universe, "Knee Replacement", video, 0.9), match(universe, "IVF", video, 0.75)]

    def blank(ref_id):
        return blank_match(known[ref_id], video, universe, "e", NOW)

    rules = [
        OverrideRule(
            topic_ref_id=by_label["IVF"].id,
            verdict="reassign",
            reassigned_to_ref_id=by_label["Cardiac Bypass Surgery"].id,
        ),
        OverrideRule(topic_ref_id=by_label["heart attack"].id, verdict="reject"),
    ]
    result = {item.topic_ref_id: item for item in apply_rules(current, rules, known, blank, NOW)}
    assert result[by_label["IVF"].id].decision == "rejected"
    bypass = result[by_label["Cardiac Bypass Surgery"].id]
    assert (bypass.decision, bypass.method, bypass.confidence) == ("accepted", MatchMethod.override, "high")
    assert result[by_label["Knee Replacement"].id].decision == "accepted"
    assert result[by_label["heart attack"].id].decision == "rejected"
    unknown = [OverrideRule(topic_ref_id="tr_ffffffffffff", verdict="accept")]
    assert len(apply_rules(current, unknown, known, blank, NOW)) == 2


async def test_review_priority_is_impact_times_uncertainty(universe) -> None:
    video = prepare(video_text("Knee replacement cost"))
    flagged = match(universe, "Knee Replacement", video, 0.5).model_copy(
        update={"review_reasons": [ReviewReason.semantic_only]}
    )
    clean = match(universe, "IVF", video, 0.9)
    items = ReviewFlagger().items([flagged, clean])
    assert len(items) == 1 and items[0]["priority"] == pytest.approx(1.0)
    assert uncertainty(0.95) == pytest.approx(0.1)


async def test_explanations_are_deterministic_templates(universe) -> None:
    video = prepare(video_text("घुटने का प्रत्यारोपण कितने का होता है?"))
    ref = refs(universe)["Knee Replacement"]
    item = match(universe, "Knee Replacement", video, 0.9).model_copy(
        update={
            "evidence": [
                MatchEvidence(
                    method="lexical",
                    field="title",
                    span_text="घुटने का प्रत्यारोपण",
                    alias_text="घुटने का प्रत्यारोपण",
                    alias_kind="translation",
                    alias_status="proposed",
                    match_quality=1.0,
                )
            ]
        }
    )
    text = MatchExplainer().explain(item, ref, footer_removed=True)
    assert text == (
        "Matched to your offering 'Knee Replacement': the title contains 'घुटने का प्रत्यारोपण' "
        "(translation, suggested and not yet confirmed); channel footer ignored "
        "(medium confidence, based on title, description and tags only)."
    )


def test_platt_and_threshold_fitting() -> None:
    margins = [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    labels = [0, 0, 0, 0, 1, 1, 1, 1]
    a, b = fit_platt(margins, labels)
    assert a > 0 and -a * 0.15 < b < 0
    accept, reject = pick_thresholds(
        [0.1, 0.2, 0.4, 0.6, 0.8, 0.9], [0, 0, 1, 0, 1, 1], min_precision=0.9, max_recall_loss=0.0
    )
    assert accept == pytest.approx(0.8)
    assert reject == pytest.approx(0.4)
    metrics = pair_metrics({("v1", "a"), ("v1", "b")}, {("v1", "a"), ("v2", "a")})
    assert (metrics.precision, metrics.recall) == (0.5, 0.5)
    assert expected_calibration_error([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0]) == pytest.approx(0.1)
