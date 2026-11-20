import json

from app.domain.recommendations import ASSIGNMENT_REC_TYPES, StrictRecommendation, recommendation_id
from tests.contracts.factories import make_recommendation, make_reference_video, make_views_at_age

ASSIGNMENT_FIELDS = {
    "id",
    "type",
    "summary",
    "lens",
    "buyer_question",
    "why_it_works",
    "reference_videos",
    "fit_to_client",
    "expected_range_vs_baseline",
    "confidence",
    "window_days",
    "generated_at",
}
ASSIGNMENT_REFERENCE_FIELDS = {"video_id", "channel", "published_at", "views_day7", "outlier_ratio"}
ASSIGNMENT_CONFIDENCE_FIELDS = {"label", "outliers", "channels"}

# transcribed from the assignment's output contract example; only the id is made to match our pattern
ASSIGNMENT_EXAMPLE = {
    "id": recommendation_id("assignment-example"),
    "type": "video_idea",
    "summary": "Short explainer on implant costs in [city], under 8 minutes",
    "lens": ["views", "conversion"],
    "buyer_question": "How much do dental implants cost near me?",
    "why_it_works": [
        "4 of 6 niche outliers in 30 days are cost explainers",
        "2 competitors cover this; you don't",
    ],
    "reference_videos": [
        {
            "video_id": "aaaaaaaaaa1",
            "channel": "Smile Dental",
            "published_at": "2026-11-02",
            "views_day7": 18400,
            "outlier_ratio": 3.6,
        }
    ],
    "fit_to_client": "Matches 2 tracked buyer questions; your how-tos average 1.4x your baseline",
    "expected_range_vs_baseline": "1.2x-2.0x (indicative)",
    "confidence": {"label": "High", "outliers": 6, "channels": 4},
    "window_days": 30,
    "generated_at": "2026-11-19T09:00:00Z",
}


def test_strict_dump_has_exactly_the_assignment_fields():
    strict = make_recommendation().strict_contract().model_dump(mode="json")

    assert set(strict) == ASSIGNMENT_FIELDS
    assert set(strict["confidence"]) == ASSIGNMENT_CONFIDENCE_FIELDS
    for reference in strict["reference_videos"]:
        assert set(reference) == ASSIGNMENT_REFERENCE_FIELDS


def test_strict_schema_properties_and_required_match_the_assignment():
    schema = StrictRecommendation.model_json_schema()
    definitions = schema["$defs"]

    assert set(schema["properties"]) == ASSIGNMENT_FIELDS
    assert set(schema["required"]) == ASSIGNMENT_FIELDS
    assert set(definitions["StrictReferenceVideo"]["properties"]) == ASSIGNMENT_REFERENCE_FIELDS
    assert set(definitions["StrictReferenceVideo"]["required"]) == ASSIGNMENT_REFERENCE_FIELDS
    assert set(definitions["StrictConfidence"]["properties"]) == ASSIGNMENT_CONFIDENCE_FIELDS


def test_assignment_example_validates_and_serialises_unchanged():
    strict = StrictRecommendation.model_validate(ASSIGNMENT_EXAMPLE)

    assert json.loads(strict.model_dump_json()) == ASSIGNMENT_EXAMPLE


def test_full_contract_keeps_assignment_fields_and_adds_only_extras():
    full = make_recommendation().model_dump(mode="json")

    assert set(full) == ASSIGNMENT_FIELDS | {"extras"}
    assert set(full["reference_videos"][0]) >= ASSIGNMENT_REFERENCE_FIELDS
    assert set(full["confidence"]) >= ASSIGNMENT_CONFIDENCE_FIELDS
    assert full["extras"]["contract_version"] == "1.1"


def test_strict_view_is_a_projection_of_the_full_card():
    reference = make_reference_video(
        0, views_day7=None, views_at_age=make_views_at_age(age_days=3, views=9_000)
    )
    card = make_recommendation(reference_videos=[reference])
    strict = card.strict_contract()

    assert strict.id == card.id
    assert strict.reference_videos[0].published_at == card.reference_videos[0].published_at.date()
    assert strict.reference_videos[0].views_day7 is None
    assert strict.confidence.label == card.confidence.label


def test_stripping_extras_from_a_full_dump_yields_a_valid_strict_contract():
    full = make_recommendation().model_dump(mode="json")
    full.pop("extras")
    full["confidence"] = {key: full["confidence"][key] for key in ASSIGNMENT_CONFIDENCE_FIELDS}
    full["reference_videos"] = [
        {key: reference[key] for key in ASSIGNMENT_REFERENCE_FIELDS} for reference in full["reference_videos"]
    ]
    for reference in full["reference_videos"]:
        reference["published_at"] = reference["published_at"][:10]

    assert StrictRecommendation.model_validate(full) == make_recommendation().strict_contract()


def test_assignment_types_are_a_subset_of_ours():
    assert {rec_type.value for rec_type in ASSIGNMENT_REC_TYPES} == {
        "video_idea",
        "title_pattern",
        "thumbnail_pattern",
        "format",
        "creator",
    }
