import re
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.competitors import CompetitorSet
from app.domain.confidence import Confidence, ConfidenceBasis, ConfidenceLabel
from app.domain.profile import ClaimMeta, ClaimOrigin, ClaimStatus, Evidence
from app.domain.recommendations import (
    RECOMMENDATION_ID_PATTERN,
    PatternSpec,
    RecommendationDraft,
    RecType,
    RefreshSubject,
    make_stable_key,
    recommendation_id,
)
from app.domain.refresh import ClaimLevel, InsertionAnchor, InsertionKind, InsertionOp, InsertionPosition
from app.domain.web import ExtractionKind, SourceKind
from tests.contracts.factories import (
    CHANNEL_IDS,
    CLIENT_ID,
    GENERATED_AT,
    make_recommendation,
    make_reference_video,
    make_views_at_age,
)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fewer_than_three_references_is_a_gate_concern_not_a_model_error(count: int):
    card = make_recommendation(reference_count=count)

    assert len(card.reference_videos) == count


def test_ids_derive_from_stable_key_and_match_the_pattern():
    key = make_stable_key(CLIENT_ID, RecType.video_idea, "topic:x", 30)

    assert recommendation_id(key) == recommendation_id(key)
    assert recommendation_id(key) != recommendation_id(
        make_stable_key(CLIENT_ID, RecType.video_idea, "topic:x", 7)
    )
    assert re.fullmatch(RECOMMENDATION_ID_PATTERN, recommendation_id(key))


@pytest.mark.parametrize("bad_id", ["rec_123", "REC_abcdefghijkl", "rec_abcdefghijk1", "abcdefghijklmnop"])
def test_malformed_ids_are_rejected(bad_id: str):
    with pytest.raises(ValidationError):
        make_recommendation(id=bad_id)


def test_id_must_match_its_stable_key():
    with pytest.raises(ValidationError, match="stable_key"):
        make_recommendation(id=recommendation_id("someone-else"))


def test_views_day7_is_nullable_when_comparison_age_is_not_seven():
    reference = make_reference_video(views_day7=None, views_at_age=make_views_at_age(age_days=3, views=9_000))

    assert reference.views_day7 is None
    assert make_recommendation(reference_videos=[reference]).reference_videos[0].views_day7 is None


@pytest.mark.parametrize(
    ("age_days", "method", "views"),
    [(3, AgeMethod.observed, 18_400), (7, AgeMethod.age_band_proxy, 18_400), (7, AgeMethod.observed, 9_999)],
)
def test_views_day7_requires_a_true_day7_value(age_days: int, method: AgeMethod, views: int):
    with pytest.raises(ValidationError, match="views_day7"):
        make_reference_video(views_day7=18_400, views_at_age=make_views_at_age(age_days, views, method))


def test_views_at_age_is_null_exactly_for_pending_or_unavailable():
    assert ViewsAtAgeSummary(age_days=7, views=None, method=AgeMethod.pending, label="pending").views is None
    with pytest.raises(ValidationError):
        ViewsAtAgeSummary(age_days=7, views=None, method=AgeMethod.observed, label="views at day 7")
    with pytest.raises(ValidationError):
        ViewsAtAgeSummary(age_days=7, views=10, method=AgeMethod.unavailable, label="unavailable")


def test_unknown_fields_are_forbidden_on_the_contract():
    card = make_recommendation().model_dump()
    card["rank"] = 1
    with pytest.raises(ValidationError, match="rank"):
        type(make_recommendation()).model_validate(card)

    card = make_recommendation().model_dump()
    card["reference_videos"][0]["score"] = 0.9
    with pytest.raises(ValidationError, match="score"):
        type(make_recommendation()).model_validate(card)


def test_contract_objects_are_immutable():
    card = make_recommendation()
    with pytest.raises(ValidationError):
        card.summary = "changed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_days": 14},
        {"lens": []},
        {"lens": ["views", "views"]},
        {"summary": "x" * 141},
        {"why_it_works": ["a", "b", "c", "d"]},
        {"why_it_works": []},
        {"fit_to_client": ""},
    ],
)
def test_contract_field_limits(overrides: dict):
    with pytest.raises(ValidationError):
        make_recommendation(**overrides)


def test_buyer_question_and_expected_range_are_nullable():
    card = make_recommendation(buyer_question=None, expected_range_vs_baseline=None)

    assert card.buyer_question is None
    assert card.expected_range_vs_baseline is None


def test_high_confidence_needs_five_outliers_across_three_channels():
    assert Confidence(label=ConfidenceLabel.high, outliers=5, channels=3).label is ConfidenceLabel.high
    with pytest.raises(ValidationError, match="High"):
        Confidence(label=ConfidenceLabel.high, outliers=4, channels=3)
    with pytest.raises(ValidationError, match="High"):
        Confidence(label=ConfidenceLabel.high, outliers=9, channels=2)


def test_first_mover_confidence_is_always_low():
    with pytest.raises(ValidationError, match="first-mover"):
        Confidence(label=ConfidenceLabel.medium, outliers=3, channels=2, basis=ConfidenceBasis.first_mover)


def test_news_cards_need_news_extras():
    with pytest.raises(ValidationError, match="extras.news"):
        make_recommendation(type=RecType.news_opportunity)


def test_draft_subject_must_match_its_type():
    pattern = PatternSpec(
        kind="title",
        traits={"is_question": "true"},
        scope="niche",
        lift=2.1,
        ci_low=1.3,
        ci_high=3.0,
        n_with=40,
        n_outliers_with=9,
        n_total=200,
        n_outliers_total=14,
    )
    fields = {
        "draft_id": "d1",
        "stable_key": "k",
        "client_id": CLIENT_ID,
        "window_days": 30,
        "as_of": GENERATED_AT,
        "produced_by": "title_pattern@1",
        "subject": pattern,
    }

    assert RecommendationDraft(type=RecType.title_pattern, **fields).subject == pattern
    with pytest.raises(ValidationError, match="subject"):
        RecommendationDraft(type=RecType.video_idea, **fields)


def _insert(text: str = "Update (Nov 2026): Nova Atlas 5.2 is now available", **overrides) -> InsertionOp:
    fields = {
        "op_id": "op1",
        "kind": InsertionKind.insert,
        "position": InsertionPosition.top_line,
        "anchor": InsertionAnchor(strategy="top", anchor_excerpt="How to make money", anchor_hash="h"),
        "text": text,
        "text_bytes": len(text.encode("utf-8")),
        "claim_level": ClaimLevel.l1_context,
        "phrased_by": "template",
    }
    fields.update(overrides)
    return InsertionOp(**fields)


def test_refresh_insertions_are_single_additive_lines():
    assert _insert().kind is InsertionKind.insert
    with pytest.raises(ValidationError, match="single line"):
        _insert("first line\nsecond line")
    with pytest.raises(ValidationError, match="never replace"):
        _insert(old_text="old")
    with pytest.raises(ValidationError, match="text_bytes"):
        _insert(text_bytes=1)
    with pytest.raises(ValidationError, match="old_text"):
        _insert(kind=InsertionKind.link_fix)


def test_refresh_subject_allows_at_most_three_operations():
    ops = [_insert(op_id=f"op{i}") for i in range(3)]
    assert len(RefreshSubject(video_id="aaaaaaaaaa1", operations=ops, review_required=False).operations) == 3
    with pytest.raises(ValidationError, match="at most 3"):
        RefreshSubject(video_id="aaaaaaaaaa1", operations=[*ops, _insert(op_id="op4")], review_required=False)


def test_competitor_set_enforces_three_to_ten_competitors():
    fields = {"client_id": CLIENT_ID, "version": 1, "confirmed_by": "user", "confirmed_at": GENERATED_AT}
    channel_ids = [f"UC{index:022d}" for index in range(11)]

    assert len(CompetitorSet(competitor_channel_ids=channel_ids[:3], **fields).competitor_channel_ids) == 3
    with pytest.raises(ValidationError):
        CompetitorSet(competitor_channel_ids=channel_ids[:2], **fields)
    with pytest.raises(ValidationError):
        CompetitorSet(competitor_channel_ids=channel_ids, **fields)
    with pytest.raises(ValidationError, match="both"):
        CompetitorSet(competitor_channel_ids=channel_ids[:3], niche_channel_ids=channel_ids[:1], **fields)


def test_third_party_and_archived_evidence_is_flagged_for_confirmation():
    base = {
        "extraction": ExtractionKind.page_text,
        "source_url": "https://example.com/services",
        "quote": "We offer robotic knee replacement",
        "match_score": 100.0,
        "fetched_at": GENERATED_AT,
    }

    assert Evidence(source_kind=SourceKind.web_mentions, **base).needs_confirmation
    assert not Evidence(source_kind=SourceKind.site_direct, **base).needs_confirmation
    with pytest.raises(ValidationError, match="snapshot_at"):
        Evidence(source_kind=SourceKind.archive, **base)
    archived = Evidence(
        source_kind=SourceKind.archive, snapshot_at=GENERATED_AT - timedelta(days=400), **base
    )
    assert archived.needs_confirmation


def test_extracted_claims_need_evidence_and_inferred_claims_need_basis():
    fields = {
        "status": ClaimStatus.proposed,
        "confidence": 0.5,
        "first_seen_version": 1,
        "updated_at": GENERATED_AT,
    }
    with pytest.raises(ValidationError, match="evidence"):
        ClaimMeta(origin=ClaimOrigin.extracted, **fields)
    with pytest.raises(ValidationError, match="basis_ids"):
        ClaimMeta(origin=ClaimOrigin.inferred, **fields)
    assert ClaimMeta(origin=ClaimOrigin.inferred, basis_ids=["F0001"], **fields).basis_ids == ["F0001"]


def test_channel_and_video_ids_are_validated():
    with pytest.raises(ValidationError):
        make_reference_video(channel_id="not-a-channel")
    with pytest.raises(ValidationError):
        make_reference_video(video_id="short")
    assert make_reference_video(channel_id=CHANNEL_IDS[0]).channel_id == CHANNEL_IDS[0]
