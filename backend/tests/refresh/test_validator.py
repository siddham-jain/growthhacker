from dataclasses import replace

import pytest

from app.core.config import RefreshSettings
from app.domain.refresh import ClaimLevel, InsertionKind, TriggerEvidence
from app.services.refresh.evidence import build_evidence
from app.services.refresh.ops.templates import TemplateLine, template_line
from app.services.refresh.ops.validator import InsertionValidator, is_repeat
from app.services.refresh.policy.claims import asserted_level
from app.services.refresh.policy.domain import DefaultPolicy, HealthcarePolicy
from app.services.refresh.triggers.base import Finding, TriggerKind
from tests.refresh import scenarios
from tests.refresh.fixtures import video

SETTINGS = RefreshSettings()
TEMPLATE = "Update (Nov 2026): Claude Fable 5.2 is now available. This video was recorded before its release."


def news_finding(level: ClaimLevel = ClaimLevel.l1_context) -> Finding:
    return Finding(
        kind=TriggerKind.news_entity,
        video_id=scenarios.AI_VIDEO,
        dedupe_key="news_entity:claude fable 5 2",
        strength=0.9,
        demand=1.0,
        urgency=0.8,
        evidence=TriggerEvidence(),
        keyword=scenarios.FABLE,
        claim_level=level,
    )


@pytest.fixture
def ai_evidence():
    record = scenarios.runable().own_videos[0]
    return build_evidence(record, frozenset(), [], 0.6)


def check(text: str, evidence, *, phrased_by: str = "llm", finding: Finding | None = None, policy=None):
    template = template_line(TEMPLATE, keywords_added=(scenarios.FABLE,), rephrasable=True)
    validator = InsertionValidator(SETTINGS, policy or DefaultPolicy())
    return validator.validate(
        replace(template, text=text),
        finding=finding or news_finding(),
        evidence=evidence,
        phrased_by=phrased_by,
        template_text=TEMPLATE,
        references=["Claude Fable 5.2 first look: everything that changed in the new model today"],
    )


def test_template_line_passes(ai_evidence) -> None:
    assert check(TEMPLATE, ai_evidence, phrased_by="template") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Update (Nov 2026): Claude Fable 5.2 is now available. This video was recorded before its release, 40% faster.",
            "adds numbers not in the evidence",
        ),
        (
            "Update (Nov 2026): Claude Fable 5.2 is now available at https://evil.example. Recorded before its release.",
            "adds a link we did not provide",
        ),
        (
            "Update (Nov 2026): Claude Fable 5.2 and Gemini Ultra are now available. Recorded before its release.",
            "doesn't support: gemini, ultra",
        ),
        (
            "Update (Nov 2026): Claude Fable 5.2 is now available — the workflow shown here applies to it.",
            "claims more than the evidence allows",
        ),
        ("AI tools, AI agents, make money, Claude Fable 5.2, templates", "reads like a keyword list"),
        ("Update (Nov 2026): Claude Fable 5.2 <script>alert(1)</script>", "contains < or >"),
        ("Update (Nov 2026): Claude Fable 5.2\nis now available.", "must be a single line"),
        (
            "Mise à jour (nov. 2026) : Claude Fable 5.2 est maintenant disponible, vidéo enregistrée avant.",
            "written in fr",
        ),
        ("Update (Nov 2026): now available, recorded before its release.", "missing the keyword"),
        (
            "Update (Nov 2026): Claude Fable 5.2 first look everything that changed in the new model today.",
            "copies wording from a reference video",
        ),
        (
            "In this video I share my exact workflow with Claude Fable 5.2: 7 ways to make money with AI tools in 2026.",
            "rewrites an existing line",
        ),
        (
            "Update (Nov 2026): Claude Fable 5.2 is guaranteed. Recorded before its release.",
            "claim term 'guaranteed'",
        ),
    ],
)
def test_adversarial_lines_are_rejected(ai_evidence, text: str, expected: str) -> None:
    problems = check(text, ai_evidence)
    assert any(expected in problem for problem in problems), problems


def test_keyword_stuffing_is_rejected(ai_evidence) -> None:
    stuffed = replace(
        ai_evidence,
        structure=replace(
            ai_evidence.structure,
            text=ai_evidence.structure.text + "\nClaude Fable 5.2 tips\nClaude Fable 5.2 more",
        ),
    )
    assert any("more than 2 times" in problem for problem in check(TEMPLATE, stuffed, phrased_by="template"))


def apollo_evidence():
    record = video(
        "apollo99999",
        title="Diabetic foot care | Apollo Hospitals Hyderabad",
        description="Diabetic foot care: daily checks and warning signs.\n\nApollo Hospitals is the leading provider.",
        published_days_ago=200,
    )
    return build_evidence(record, frozenset(), [], 0.6)


def coverage_finding() -> Finding:
    return Finding(
        kind=TriggerKind.above_fold_keyword,
        video_id="apollo99999",
        dedupe_key="above_fold_keyword:diabetic foot care",
        strength=0.7,
        demand=0.5,
        urgency=0.4,
        evidence=TriggerEvidence(),
        keyword="diabetic foot care",
        claim_level=ClaimLevel.l2_coverage,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Diabetic foot care cures ulcers permanently.", "medical claim term 'cure"),
        ("Best hospital for diabetic foot care in Hyderabad.", "superlative 'best'"),
        ("Diabetic foot care to prevent diabetes complications.", "scheduled condition"),
        ("Diabetic foot care with Dr. Reddy, MBBS.", "doctor name"),
        ("Painless diabetic foot care.", "medical claim term 'painless'"),
    ],
)
def test_healthcare_pack_blocks_medical_claims(text: str, expected: str) -> None:
    evidence = apollo_evidence()
    line = TemplateLine(text=text, keywords_added=("diabetic foot care",), rephrasable=True)
    problems = InsertionValidator(SETTINGS, HealthcarePolicy()).validate(
        line, finding=coverage_finding(), evidence=evidence, phrased_by="template", template_text=text
    )
    assert any(expected in problem for problem in problems), problems


def test_healthcare_allows_superlatives_already_in_the_description() -> None:
    policy = HealthcarePolicy()
    assert (
        policy.violations(
            "Apollo Hospitals is the leading provider.", "Apollo Hospitals is the leading provider."
        )
        == []
    )
    assert policy.violations("The leading clinic", "Nothing here") != []


def test_healthcare_never_permits_applicability_wording() -> None:
    policy = HealthcarePolicy()
    assert not policy.permits_claim("news_entity", ClaimLevel.l3_applicability)
    assert not policy.permits_claim("above_fold_keyword", ClaimLevel.l1_context)
    assert policy.permits_claim("missing_cta", ClaimLevel.l0_navigational)


def test_link_fix_shape_is_enforced(ai_evidence) -> None:
    line = TemplateLine(
        text="https://www.facebook.com/TheApolloHospitals/",
        kind=InsertionKind.link_fix,
        old_text="Follow us on Facebook",
        allowed_urls=frozenset({"https://www.facebook.com/TheApolloHospitals/"}),
    )
    problems = InsertionValidator(SETTINGS, DefaultPolicy()).validate(
        line,
        finding=news_finding(ClaimLevel.l0_navigational),
        evidence=ai_evidence,
        phrased_by="template",
        template_text="",
    )
    assert "a link fix may only replace a link" in problems


def test_claim_levels() -> None:
    assert asserted_level(TEMPLATE) is ClaimLevel.l1_context
    assert asserted_level("Book an appointment: https://example.com/book") is ClaimLevel.l0_navigational
    assert asserted_level("This video covers knee pain") is ClaimLevel.l3_applicability
    assert asserted_level("Knee pain: when to see a doctor.") is ClaimLevel.l2_coverage


def test_same_sentence_on_many_videos_counts_as_repetition() -> None:
    assert is_repeat(TEMPLATE, [TEMPLATE] * 3, SETTINGS)
    assert not is_repeat(TEMPLATE, [TEMPLATE] * 2, SETTINGS)
