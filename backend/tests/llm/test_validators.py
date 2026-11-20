import copy as copylib
from typing import Any

import pytest

from app.core.config import CardWriterSettings
from app.domain.recommendations import RecType
from app.services.llm.writer.evidence_pack import EvidencePackBuilder
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.template import to_card_copy
from app.services.llm.writer.validators import ValidatorSuite, blocking
from tests.llm.factories import GOOD_COPY, TITLES, make_client_context, make_draft, pattern_subject

SUITE = ValidatorSuite(CardWriterSettings())


def built_pack(**client_overrides: Any):
    return EvidencePackBuilder().build(make_draft(), make_client_context(**client_overrides))


def rules(copy: dict[str, Any], **client_overrides: Any) -> set[str]:
    violations = blocking(SUITE.run(CardCopyLLM.model_validate(copy), built_pack(**client_overrides)))
    return {violation.rule for violation in violations}


def variant(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    data = copylib.deepcopy(GOOD_COPY)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_good_copy_passes_every_validator() -> None:
    assert rules(GOOD_COPY) == set()


def test_soft_entity_warnings_do_not_block() -> None:
    copy = variant(("summary",), "Make a short explainer on Whitening Myths for your clinic")
    violations = SUITE.run(CardCopyLLM.model_validate(copy), built_pack())
    assert any(v.rule == "unverified_entity" for v in violations)
    assert blocking(violations) == []


REASON_1 = ("why_it_works", 0, "text")
ADVERSARIAL = [
    (
        "hallucinated number",
        REASON_1,
        "Cost videos reached 25K views in the last 30 days",
        "number_not_in_cited_fact",
    ),
    (
        "derived percent",
        REASON_1,
        "67% of competitor outliers in the last 30 days answer a cost question",
        "number_not_in_cited_fact",
    ),
    (
        "number from another fact",
        REASON_1,
        "4 of 6 competitor outliers reached 18.4K views",
        "number_not_in_cited_fact",
    ),
    (
        "rounded up",
        ("why_it_works", 1, "text"),
        "Cost videos reached 3.9x their channel baseline at day 7",
        "number_not_in_cited_fact",
    ),
    (
        "extra channel",
        ("summary",),
        "Copy what Dr Mehta Dental does with cost explainers",
        "unknown_channel_or_entity",
    ),
    ("video id", ("summary",), "Remake the cost explainer aB3dE5fG7hJ for your clinic", "video_id_in_text"),
    ("video url", ("summary",), "Answer cost like youtube.com/watch?v=aaaaaaaaaa1 does", "video_id_in_text"),
    (
        "foreign domain",
        ("summary",),
        "Answer whitening cost questions like brightsmile.com does",
        "unknown_domain",
    ),
    (
        "alias in text",
        REASON_1,
        "As f1 shows, 4 of 6 competitor outliers answer a cost question",
        "alias_in_text",
    ),
    ("copied title", ("summary",), TITLES[0], "copies_title"),
    ("copied recent title", ("working_title",), "A tour of our new Pune clinic", "copies_title"),
    (
        "demographics",
        ("summary",),
        "Your audience is mostly women so explain whitening cost",
        "policy_violation",
    ),
    ("revenue", ("summary",), "Cost explainers earn ad revenue for dental channels", "policy_violation"),
    (
        "face descriptor",
        ("summary",),
        "Use a smiling face thumbnail for a whitening cost video",
        "policy_violation",
    ),
    ("promise", ("summary",), "This whitening cost video will drive more bookings", "promise"),
    ("banned hype", ("summary",), "A game-changer whitening cost explainer for your clinic", "banned_phrase"),
    ("healthcare claim", ("summary",), "Explain painless whitening and what it costs", "regulated_claim"),
    ("wrong language", ("summary",), "दांतों को सफेद करने की लागत पर एक वीडियो बनाइए", "wrong_language"),
    ("too long", ("summary",), "Make a video " + "about whitening cost " * 10, "too_long"),
    ("unsupported reason", REASON_1, "Patients love friendly receptionists", "reason_not_supported"),
]


@pytest.mark.parametrize(
    ("case", "path", "value", "rule"), ADVERSARIAL, ids=[case[0] for case in ADVERSARIAL]
)
def test_adversarial_outputs_fail_with_the_expected_rule(
    case: str, path: tuple[Any, ...], value: str, rule: str
) -> None:
    assert rule in rules(variant(path, value))


def test_unknown_alias_and_duplicates_are_rejected() -> None:
    copy = copylib.deepcopy(GOOD_COPY)
    copy["why_it_works"] = [
        {"fact_refs": ["f9"], "text": "4 of 6 competitor outliers answer a cost question"},
        {
            "fact_refs": ["f1"],
            "text": "4 of 6 competitor outliers in the last 30 days answer a cost question",
        },
        {"fact_refs": ["f1"], "text": "4 of 6 competitor outliers in the last 30 days answer cost questions"},
    ]
    found = rules(copy)
    assert {"unknown_fact_alias", "duplicate_reason"} <= found


def test_fit_must_cite_a_fit_fact() -> None:
    copy = variant(("fit_to_client", "fact_refs"), ["f1"])
    assert "fit_without_fit_fact" in rules(copy)


def test_reason_count_bounds() -> None:
    assert "too_few_reasons" in rules(variant(("why_it_works",), GOOD_COPY["why_it_works"][:1]))


def test_conversion_claims_need_own_analytics() -> None:
    copy = variant(("summary",), "Cost explainers have the best conversion rate in the niche")
    assert "policy_violation" in rules(copy)


def test_numbers_inside_allowed_names_are_masked() -> None:
    copy = variant(("summary",), "Answer whitening cost for the 24/7 Smile Desk at your clinic")
    assert rules(copy, offerings=["Teeth whitening", "24/7 Smile Desk"]) == set()


def test_expected_range_may_be_quoted_verbatim() -> None:
    copy = variant(("summary",), "Make a whitening cost explainer; expect 1.2x-2.0x (indicative)")
    assert rules(copy) == set()


def test_hindi_requires_devanagari() -> None:
    copy = variant(("summary",), "दांतों को सफेद करने की लागत पर एक छोटा वीडियो बनाइए")
    for key, text in (
        (0, "प्रतिस्पर्धियों के 6 में से 4 आउटलायर पिछले 30 दिनों में लागत का सवाल हल करते हैं"),
        (1, "लागत वाले वीडियो दिन 7 पर अपने बेसलाइन के 3.6x तक पहुंचे"),
    ):
        copy["why_it_works"][key]["text"] = text
    copy["working_title"] = None
    copy["fit_to_client"]["text"] = (
        'आपके ग्राहक पूछते हैं "How much does teeth whitening cost?" और आपके चैनल पर इसका वीडियो नहीं है'
    )
    violations = blocking(SUITE.run(CardCopyLLM.model_validate(copy), built_pack(language="hi")))
    assert "wrong_language" not in {violation.rule for violation in violations}
    english = blocking(SUITE.run(CardCopyLLM.model_validate(GOOD_COPY), built_pack(language="hi")))
    assert "wrong_language" in {violation.rule for violation in english}


def test_vertical_rules_apply_only_to_matching_verticals() -> None:
    copy = variant(("summary",), "Explain painless whitening and what it costs")
    assert "regulated_claim" not in rules(copy, vertical="home services")


def test_title_pattern_cards_drop_working_titles() -> None:
    draft = make_draft(RecType.title_pattern, subject=pattern_subject())
    built = EvidencePackBuilder().build(draft, make_client_context())
    card = to_card_copy(
        CardCopyLLM.model_validate(GOOD_COPY),
        built,
        copy_source="llm",
        prompt_id="card_write",
        prompt_version="v1",
        model="m",
        attempts=1,
        violations=[],
    )
    assert card.working_title is None
    assert card.reason_fact_ids == [["r_share"], ["r_ratio"]]
    assert card.fit_fact_ids == ["r_question"]
