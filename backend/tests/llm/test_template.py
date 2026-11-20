import pytest

from app.core.config import CardWriterSettings
from app.domain.recommendations import RecType
from app.services.llm.writer.evidence_pack import EvidencePackBuilder
from app.services.llm.writer.template import TemplateCardWriter
from app.services.llm.writer.validators import ValidatorSuite, blocking
from tests.llm.factories import (
    DENTAL_REASONS,
    creator_subject,
    make_client_context,
    make_draft,
    news_subject,
    number,
    pattern_subject,
    reason,
)

SUITE = ValidatorSuite(CardWriterSettings())
HOSPITAL_REASONS = [
    reason(
        "h_share",
        "5 of 8 competitor outliers this month explain knee replacement recovery",
        [number(5, "count", "5"), number(8, "count", "8")],
    ),
    reason(
        "h_views",
        "The top recovery video reached 1.84 lakh views at day 7",
        [number(184_000, "views", "1.84 lakh")],
    ),
]
OWN_FIT = [
    reason(
        "h_own",
        "Your recovery videos earned 2.4x your baseline in the last 90 days",
        [number(2.4, "ratio_x", "2.4x"), number(90, "days", "90 days")],
        source="youtube_own_analytics",
    )
]

CASES = {
    "dental video idea": (make_draft, {}, {}),
    "dental title pattern": (
        make_draft,
        {"rec_type": RecType.title_pattern, "subject": pattern_subject()},
        {},
    ),
    "dental creator": (make_draft, {"rec_type": RecType.creator, "subject": creator_subject()}, {}),
    "dental news": (
        make_draft,
        {
            "rec_type": RecType.news_opportunity,
            "subject": news_subject(),
            "entities": ["Fluoride Advisory 2026"],
        },
        {},
    ),
    "hospital with own analytics": (
        make_draft,
        {"reasons": HOSPITAL_REASONS, "fit_reasons": OWN_FIT, "buyer_question_id": None},
        {"name": "Lakeside Hospital", "vertical": "multi-speciality hospital", "buyer_questions": {}},
    ),
    "auto dealer without fit facts": (
        make_draft,
        {"reasons": DENTAL_REASONS[:1], "fit_reasons": []},
        {"name": "Northside Motors", "vertical": "auto dealer"},
    ),
    "hindi client falls back to english": (make_draft, {}, {"language": "hi"}),
}


@pytest.mark.parametrize("case", list(CASES))
def test_template_copy_never_violates(case: str) -> None:
    factory, draft_overrides, client_overrides = CASES[case]
    built = EvidencePackBuilder().build(factory(**draft_overrides), make_client_context(**client_overrides))
    template = TemplateCardWriter()
    draft_copy = template.draft(built)
    if built.pack.language == "en":
        assert blocking(SUITE.run(draft_copy, built)) == []
    card = template.write(built, degrade_reason="llm_unavailable")
    assert card.copy_source == "template"
    assert card.language == "en"
    assert card.why_it_works
    assert all(ids for ids in card.reason_fact_ids)


def test_template_uses_the_buyer_question_for_video_ideas() -> None:
    built = EvidencePackBuilder().build(make_draft(), make_client_context())
    card = TemplateCardWriter().write(built, degrade_reason="budget")
    assert card.summary == 'Make a video answering "How much does teeth whitening cost?"'
    assert card.why_it_works[0] == DENTAL_REASONS[0].statement
    assert card.degrade_reason == "budget"
