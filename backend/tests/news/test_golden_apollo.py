from datetime import timedelta

from app.domain.news import EventType, RelevanceBand, SafetyFlag, SourceTier
from app.services.news.opportunity import SURFACE_REVIEW
from app.services.news.safety import HEALTH_DISCLAIMER
from app.services.news.scanner import ScanInputs
from tests.news.fixtures import (
    NOW,
    FakeGateway,
    ScriptedLLM,
    apollo_context,
    bundles,
    demand_checker,
    entity,
    judgement,
    news_item,
    scanner,
)

ADVISORY_QUOTE = "seek care for warning signs of dengue"


def items():
    return [
        news_item(
            "https://mohfw.gov.in/advisory/dengue-2026",
            "Health Ministry issues dengue advisory as cases rise",
            f"The Ministry advises people to {ADVISORY_QUOTE} such as severe abdominal pain or bleeding.",
            NOW - timedelta(hours=20),
        ),
        news_item(
            "https://www.thehindu.com/news/national/health-ministry-dengue-advisory/article1.ece",
            "Health Ministry issues dengue advisory as cases rise in states",
            "States were asked to prepare hospitals for a seasonal surge in dengue.",
            NOW - timedelta(hours=18),
        ),
        news_item(
            "https://timesofindia.indiatimes.com/city/kanpur/dengue-checks/articleshow/1.cms",
            "Kanpur steps up dengue checks after dengue advisory",
            "Civic teams inspected 400 homes for larvae on Sunday.",
            NOW - timedelta(hours=30),
        ),
        news_item(
            "https://www.medplusmart.com/blog/dengue-vaccine-approval-india",
            "Dengue vaccine approved in India: what you need to know",
            "After the dengue advisory, a new dengue vaccine is said to cure severe infection.",
            NOW - timedelta(hours=26),
        ),
        news_item(
            "https://www.health.vic.gov.au/news/dengue-advisory",
            "Victoria issues dengue advisory for travellers",
            "Travellers returning from Bali should watch for fever.",
            NOW - timedelta(hours=22),
        ),
    ]


def script():
    dengue = {"kind": "condition"}
    return {
        "Health Ministry issues dengue advisory": judgement(
            entity("dengue", "n1", ADVISORY_QUOTE, **dengue),
            band=RelevanceBand.strong,
            event=EventType.public_health_advisory,
            offerings=(("o1", []),),
            questions=("q1",),
            action="a1",
            region="primary_region",
            angle="Explain dengue warning signs and when to come to the emergency department",
        ),
        "Kanpur steps up dengue checks": judgement(
            entity("dengue", "n1", "Kanpur steps up dengue checks", **dengue),
            band=RelevanceBand.adjacent,
            event=EventType.public_health_advisory,
            offerings=(("o1", []),),
            region="primary_region",
        ),
        "Dengue vaccine approved in India": judgement(
            entity("dengue vaccine", "n1", "Dengue vaccine approved in India", kind="drug"),
            band=RelevanceBand.strong,
            event=EventType.regulatory_approval,
            offerings=(("o2", []),),
            flags=(SafetyFlag.medical_claim,),
            region="primary_region",
        ),
        "Victoria issues dengue advisory": judgement(
            entity("dengue", "n1", "Victoria issues dengue advisory", **dengue),
            band=RelevanceBand.adjacent,
            event=EventType.public_health_advisory,
            offerings=(("o1", []),),
            region="other_region_only",
        ),
    }


async def scan():
    ctx = apollo_context()
    llm = ScriptedLLM(script())
    outcome = await scanner(ctx, llm, demand_checker(FakeGateway())).scan(
        ScanInputs(ctx=ctx, bundles=bundles(items()), now=NOW)
    )
    return ctx, outcome


async def test_authoritative_advisory_becomes_a_reviewed_health_draft():
    _ctx, outcome = await scan()

    assert len(outcome.drafts) == 1
    draft = outcome.drafts[0]
    subject = draft.subject
    assert SURFACE_REVIEW in draft.flags and "health" in draft.flags
    assert subject.compliance.review_required is True
    assert HEALTH_DISCLAIMER in subject.compliance.disclaimers
    assert subject.conversion_action_id == "act_book"
    assert subject.format_suggestion.format == "long_explainer"
    tiers = {source.publisher: source.tier for source in subject.reference_sources}
    assert tiers == {"mohfw.gov.in": SourceTier.tier1_authority, "thehindu.com": SourceTier.tier2_established}
    signal = next(s for s in outcome.signals if s.id == subject.news_signal_id)
    assert signal.credibility.has_authoritative_health_source
    assert signal.safety.decision == "allow_with_guidance"
    assert not signal.refresh_eligible
    assert outcome.review == [(signal.id, "health_review")]


async def test_health_news_without_authority_never_reaches_a_card():
    _ctx, outcome = await scan()

    by_title = {s.sources[0].title: s for s in outcome.signals}
    toi = by_title["Kanpur steps up dengue checks after dengue advisory"]
    vaccine = by_title["Dengue vaccine approved in India: what you need to know"]
    victoria = by_title["Victoria issues dengue advisory for travellers"]
    assert toi.suppression_reason == "health_needs_authority"
    assert vaccine.suppression_reason == "low_credibility"
    assert "HEALTH_CLAIM_WITHOUT_AUTHORITY" in vaccine.safety.reasons
    assert vaccine.safety.decision == "block"
    assert victoria.status == "suppressed"
    assert victoria.relevance_score < 0.45
