import pytest

from app.domain.analytics import AgeMethod
from app.domain.confidence import ConfidenceLabel, ConfidenceRuleId
from app.domain.recommendations import ExpectedRange, RecType
from app.services.recommend.confidence import CreatorConfidenceRule, DefaultConfidenceRule
from tests.recommend.factories import draft, reference


def refs(outliers: int, channels: int, extra: int = 0, **fields):
    items = [reference(index, channel=f"ch{index % channels}", **fields) for index in range(outliers)]
    items += [reference(100 + index, channel=f"x{index}", outlier=False, **fields) for index in range(extra)]
    return items


@pytest.mark.parametrize(
    ("outliers", "channels", "label"),
    [
        (5, 3, ConfidenceLabel.high),
        (4, 3, ConfidenceLabel.medium),
        (5, 2, ConfidenceLabel.medium),
        (3, 2, ConfidenceLabel.medium),
        (2, 2, ConfidenceLabel.low),
        (3, 1, ConfidenceLabel.low),
    ],
)
def test_default_rule_boundaries(ctx, outliers: int, channels: int, label: ConfidenceLabel) -> None:
    pool = refs(
        outliers, channels
    )  # channels interleave in pool order, so the share cap stays out of the way
    confidence = DefaultConfidenceRule().label(draft(pool, references=pool[:5]), ctx)

    assert confidence.label is label
    assert (confidence.outliers, confidence.channels) == (outliers, channels)
    assert confidence.rule_id is ConfidenceRuleId.default_v1


@pytest.mark.parametrize(
    ("flags", "cap"),
    [
        (["contradictory"], "contradictory"),
        (["review_flagged_topics"], "review_flagged_topics"),
        (["single_channel"], "single_channel"),
    ],
)
def test_caps_lower_the_label_and_are_recorded(ctx, flags: list[str], cap: str) -> None:
    pool = refs(6, 6)
    confidence = DefaultConfidenceRule().label(draft(pool, references=pool[:5], flags=flags), ctx)

    assert confidence.label is not ConfidenceLabel.high
    assert cap in confidence.caps_applied


def test_one_channel_supplying_most_references_caps_at_medium(ctx) -> None:
    pool = refs(6, 6)
    shown = [reference(200 + index, channel="ch0") for index in range(4)] + [pool[1]]
    confidence = DefaultConfidenceRule().label(draft(pool, references=shown), ctx)

    assert confidence.label is ConfidenceLabel.medium
    assert "one_channel_dominates" in confidence.caps_applied


def test_proxy_evidence_drops_one_level(ctx) -> None:
    pool = refs(6, 6, method=AgeMethod.age_band_proxy)
    confidence = DefaultConfidenceRule().label(draft(pool, references=pool[:5]), ctx)

    assert confidence.label is ConfidenceLabel.medium
    assert "proxy_evidence" in confidence.caps_applied


def test_no_range_means_low(ctx) -> None:
    pool = refs(6, 6)
    insufficient = ExpectedRange(
        low=None, high=None, basis="insufficient", comparison_age_days=7, display="n/a"
    )
    confidence = DefaultConfidenceRule().label(
        draft(pool, references=pool[:5], expected_range=insufficient), ctx
    )

    assert confidence.label is ConfidenceLabel.low
    assert "range_insufficient" in confidence.caps_applied


def test_patterns_need_a_lifted_interval_for_high(ctx) -> None:
    pool = refs(6, 6)
    lifted = draft(pool, RecType.title_pattern, references=pool[:5])
    unlifted = lifted.model_copy(update={"subject": lifted.subject.model_copy(update={"ci_low": 0.9})})

    assert DefaultConfidenceRule().label(lifted, ctx).label is ConfidenceLabel.high
    assert DefaultConfidenceRule().label(unlifted, ctx).label is ConfidenceLabel.medium


def test_creator_rule_reports_one_channel_and_never_claims_high(inputs, service) -> None:
    plan = service.plan(inputs, 30)
    creator = next(item for item in plan.creators if item.subject.title == "Dr Heart Talks")
    confidence = CreatorConfidenceRule().label(creator, plan.ctx)

    assert confidence.rule_id is ConfidenceRuleId.creator_v1
    assert confidence.channels == 1
    assert confidence.label is ConfidenceLabel.medium
