from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.core.config import RecommendGatePolicy
from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.confidence import Confidence, ConfidenceLabel
from app.domain.recommendations import ExpectedRange, GateAction, RecType
from app.services.recommend.gates.structural import (
    ContradictoryEvidence,
    EvidenceCompleteness,
    FitToClient,
    HasLens,
    HonestConfidence,
    MinReferenceVideos,
    NoCopyingStructural,
    NoRepeatClient,
    PolicyGuardrailsStructural,
    ThinRange,
    UserFeedbackGate,
)
from app.services.recommend.gates.text import (
    CertaintyLanguage,
    NoCopyingText,
    NumbersFromEvidence,
    PolicyGuardrailsText,
    ReasonIntegrity,
)
from app.services.recommend.inputs import FeedbackState
from app.services.recommend.writer import TemplateCardRenderer
from tests.recommend.fixtures import AS_OF, SPECS, VIDEOS, VideoSpec, apollo_inputs, topic_id


@pytest.fixture
def plan(service, inputs):
    return service.plan(inputs, 30)


@pytest.fixture
def knee(plan):
    return next(item for item in plan.selection.selected if item.topic_ids == [topic_id("bq_knee")])


def action(gate, draft, env, written=None):
    return gate.check(draft, env, written)


def test_user_feedback_hides_dismissed_keys(plan, knee) -> None:
    env = replace(
        plan.env, ctx=replace(plan.ctx, feedback=FeedbackState(hidden_keys={knee.stable_key: "dismissed"}))
    )

    assert action(UserFeedbackGate(), knee, plan.env).action is GateAction.pass_
    assert action(UserFeedbackGate(), knee, env).reason_code == "hidden_by_feedback"


def test_structural_policy_rejects_income_or_audience_fields(plan, knee) -> None:
    bad_reason = knee.reasons[0].model_copy(update={"params": {"channel_revenue": 1200}})
    bad = knee.model_copy(update={"reasons": [bad_reason]})

    assert action(PolicyGuardrailsStructural(), knee, plan.env).action is GateAction.pass_
    assert action(PolicyGuardrailsStructural(), bad, plan.env).reason_code == "policy_field"


def test_incomplete_references_are_dropped_and_the_draft_repaired(plan, knee) -> None:
    unknown = ViewsAtAgeSummary(age_days=7, views=None, method=AgeMethod.unavailable, label="unavailable")
    broken = knee.reference_pool[0].model_copy(update={"views_at_age": unknown, "views_day7": None})
    stale = knee.reference_pool[1].model_copy(update={"published_at": AS_OF - timedelta(days=45)})
    damaged = knee.model_copy(update={"reference_pool": [broken, stale, *knee.reference_pool[2:]]})
    outcome = action(EvidenceCompleteness(), damaged, plan.env)

    assert outcome.action is GateAction.repair
    assert {broken.video_id, stale.video_id}.isdisjoint(ref.video_id for ref in outcome.draft.reference_pool)
    assert len(outcome.draft.references) >= 3
    empty = knee.model_copy(update={"reference_pool": [broken]})
    assert action(EvidenceCompleteness(), empty, plan.env).reason_code == "no_complete_refs"


def test_minimum_references_and_channels(plan, knee) -> None:
    two = knee.model_copy(update={"references": knee.references[:2]})
    pattern = next(item for item in plan.selection.selected if item.type is RecType.title_pattern)
    one_channel = pattern.model_copy(
        update={
            "references": [
                ref.model_copy(update={"channel_id": pattern.references[0].channel_id})
                for ref in pattern.references
            ]
        }
    )

    assert action(MinReferenceVideos(), knee, plan.env).action is GateAction.pass_
    assert action(MinReferenceVideos(), two, plan.env).reason_code == "too_few_refs"
    assert action(MinReferenceVideos(), one_channel, plan.env).reason_code == "single_channel_pattern"


def test_fit_needs_a_fit_reason_and_topic_matching_references(plan, knee) -> None:
    no_reason = knee.model_copy(update={"fit_reasons": []})
    off_topic = knee.model_copy(
        update={"references": [ref.model_copy(update={"matched_topic_ids": []}) for ref in knee.references]}
    )

    assert action(FitToClient(), knee, plan.env).action is GateAction.pass_
    assert action(FitToClient(), no_reason, plan.env).reason_code == "no_fit"
    assert action(FitToClient(), off_topic, plan.env).reason_code == "no_fit"


def plan_with_own(service, *specs: VideoSpec):
    return service.plan(apollo_inputs(videos=[*VIDEOS, *specs]), 30)


def test_repeat_guard(service, plan, knee) -> None:
    same_format = plan_with_own(
        service,
        VideoSpec("q1", "apollo", "Knee replacement cost at Apollo", 12, 1.2, topics={"bq_knee": None}),
    )
    other_format = plan_with_own(
        service,
        VideoSpec(
            "q2",
            "apollo",
            "Knee cost in one minute",
            60,
            1.2,
            segment="short_form",
            band="≤60s",
            topics={"bq_knee": None},
        ),
    )

    assert action(NoRepeatClient(), knee, plan.env).action is GateAction.pass_
    assert action(NoRepeatClient(), knee, same_format.env).reason_code == "client_already_published"
    annotated = action(
        NoRepeatClient(), knee.model_copy(update={"fit_reasons": knee.fit_reasons[:1]}), other_format.env
    )
    assert annotated.action is GateAction.repair
    assert annotated.draft.fit_reasons[-1].code == "CLIENT_COVERED_DIFFERENT_FORMAT"


def test_a_fresh_client_upload_on_the_topic_is_judged_at_day_seven(service) -> None:
    plan = service.plan(apollo_inputs(), 30)
    heart = [item for item in plan.suppressed if item.draft.topic_ids == [topic_id("bq_heart")]]

    assert [item.reason_code for item in heart] == ["client_recent_pending"]


def test_structural_copy_check_on_the_topic_phrase(plan, knee) -> None:
    copied = knee.model_copy(
        update={"subject": knee.subject.model_copy(update={"topic_text": SPECS["k1"].title})}
    )

    assert action(NoCopyingStructural(), knee, plan.env).action is GateAction.pass_
    assert action(NoCopyingStructural(), copied, plan.env).reason_code == "copy_risk"


def test_contradiction_with_large_samples_is_suppressed(plan) -> None:
    pattern = next(item for item in plan.selection.selected if item.type is RecType.title_pattern)

    assert action(ContradictoryEvidence(), pattern, plan.env).action is GateAction.pass_
    flagged = pattern.model_copy(update={"flags": ["contradictory", "contradictory_large_samples"]})
    assert action(ContradictoryEvidence(), flagged, plan.env).reason_code == "contradictory_evidence"


def test_inflated_confidence_is_recomputed(plan, knee) -> None:
    inflated = knee.model_copy(
        update={
            "confidence": Confidence(label=ConfidenceLabel.high, outliers=9, channels=6, matching_videos=40)
        }
    )
    outcome = action(HonestConfidence(), inflated, plan.env)

    assert action(HonestConfidence(), knee, plan.env).action is GateAction.pass_
    assert outcome.action is GateAction.downgrade
    assert outcome.draft.confidence == knee.confidence


def test_no_range_forces_low_confidence(plan, knee) -> None:
    insufficient = ExpectedRange(
        low=None, high=None, basis="insufficient", comparison_age_days=7, display="n/a"
    )
    medium = Confidence(label=ConfidenceLabel.medium, outliers=3, channels=2)
    outcome = action(
        ThinRange(), knee.model_copy(update={"expected_range": insufficient, "confidence": medium}), plan.env
    )

    assert outcome.action is GateAction.downgrade
    assert outcome.draft.confidence.label is ConfidenceLabel.low


def test_a_card_without_a_lens_is_not_shown(plan, knee) -> None:
    assert action(HasLens(), knee, plan.env).action is GateAction.pass_
    assert action(HasLens(), knee.model_copy(update={"lenses": []}), plan.env).reason_code == "no_lens"


@pytest.fixture
def written(plan, knee):
    return TemplateCardRenderer().render(knee, plan.ctx.client)


@pytest.mark.parametrize(
    "gate",
    [ReasonIntegrity(), NumbersFromEvidence(), NoCopyingText(), PolicyGuardrailsText(), CertaintyLanguage()],
)
def test_template_copy_passes_every_text_gate(plan, knee, written, gate) -> None:
    assert action(gate, knee, plan.env, written).action is GateAction.pass_


@pytest.mark.parametrize(
    ("gate", "update", "reason"),
    [
        (
            ReasonIntegrity(),
            {"reason_fact_ids": [["r9"]], "why_it_works": ["An extra reason"]},
            "unknown_reason",
        ),
        (ReasonIntegrity(), {"fit_to_client": "See https://youtube.com/watch?v=abc"}, "identifier_in_text"),
        (
            NumbersFromEvidence(),
            {"summary": "Knee cost explainers get 7.5x more views"},
            "number_not_in_evidence",
        ),
        (NoCopyingText(), {"summary": SPECS["k3"].title}, "copy_risk"),
        (PolicyGuardrailsText(), {"fit_to_client": "Fortis earns more ad revenue from these"}, "policy_text"),
        (PolicyGuardrailsText(), {"summary": "This surgery cures knee pain"}, "policy_text"),
        (CertaintyLanguage(), {"summary": "Guaranteed to go viral"}, "certainty_claim"),
        (
            CertaintyLanguage(),
            {"fit_to_client": "Expect 1.6x-1.9x your baseline"},
            "range_without_indicative",
        ),
    ],
)
def test_text_gates_reject_slop(plan, knee, written, gate, update, reason) -> None:
    assert action(gate, knee, plan.env, written.model_copy(update=update)).reason_code == reason


def test_gate_policy_keeps_the_assignment_minimum_unless_unlocked() -> None:
    with pytest.raises(ValidationError, match="3\\+ reference videos"):
        RecommendGatePolicy(min_reference_videos={"default": 3, "video_idea": 2})
    with pytest.raises(ValidationError, match="at least 1"):
        RecommendGatePolicy(min_reference_videos={"default": 3, "news_opportunity": 0})
    unlocked = RecommendGatePolicy(
        min_reference_videos={"default": 3, "video_idea": 2}, allow_below_assignment_minimum=True
    )
    assert unlocked.min_refs("video_idea") == 2 and unlocked.min_refs("format") == 3
