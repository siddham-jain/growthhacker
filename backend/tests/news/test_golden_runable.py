from datetime import timedelta

from app.domain.confidence import ConfidenceBasis, ConfidenceLabel
from app.domain.news import EventType, RelevanceBand
from app.domain.recommendations import GateAction, RecType
from app.services.news.gates import NewsEvidenceGate
from app.services.news.opportunity import SURFACE_EARLY_SIGNALS, SURFACE_RECOMMENDATIONS
from app.services.news.scanner import ScanInputs
from tests.news.fixtures import (
    NOW,
    FakeBaselines,
    FakeGateway,
    ScriptedLLM,
    bundles,
    demand_checker,
    entity,
    judgement,
    news_item,
    runable_context,
    scanner,
    video_record,
)

CHANNELS = [
    "UCaiexplained00000000001",
    "UCtechreviews0000000000b",
    "UCslidesdaily0000000000c",
    "UCagentlab000000000000dd",
]
LAUNCH_QUOTE = "Today we're releasing Claude Fable 5.2"


def launch_items():
    return [
        news_item(
            "https://www.anthropic.com/news/claude-fable-5-2",
            "Introducing Claude Fable 5.2",
            f"{LAUNCH_QUOTE}, our most capable model for agentic work, slides and research.",
            NOW - timedelta(hours=14),
        ),
        news_item(
            "https://techcrunch.com/2026/11/20/anthropic-releases-claude-fable-5-2/?utm_source=rss",
            "Anthropic releases Claude Fable 5.2 with stronger agent skills",
            "Anthropic on Thursday released Claude Fable 5.2, its newest frontier model.",
            NOW - timedelta(hours=12),
        ),
        news_item(
            "https://www.theverge.com/news/claude-fable-5-2",
            "Anthropic's Claude Fable 5.2 is here",
            "Claude Fable 5.2 arrives with better tool use and longer context.",
            NOW - timedelta(hours=11),
        ),
    ]


def ipo_items():
    return [
        news_item(
            "https://www.cnbc.com/2026/11/19/anthropic-ipo-filing.html",
            "Anthropic files confidentially for IPO",
            "The Claude maker is preparing a public listing, sources say.",
            NOW - timedelta(hours=30),
        )
    ]


def script():
    return {
        "Introducing Claude Fable 5.2": judgement(
            entity("Claude Fable 5.2", "n1", LAUNCH_QUOTE),
            others=[entity("Anthropic", "n2", "Anthropic on Thursday released Claude Fable 5.2", kind="org")],
            angle="Show Claude Fable 5.2 building a deck in Runable",
        ),
        "files confidentially for IPO": judgement(
            entity("Anthropic", "n1", "Anthropic files confidentially for IPO", kind="org"),
            band=RelevanceBand.tangential,
            event=EventType.funding_or_ipo,
            offerings=(),
            questions=(),
            action=None,
        ),
    }


def early_videos(count: int, outliers: int):
    videos = []
    for index in range(count):
        videos.append(
            video_record(
                f"fable52vid{index}",
                CHANNELS[index],
                f"Claude Fable 5.2 first look part {index + 1}",
                NOW - timedelta(hours=8 - index),
                40_000 if index < outliers else 2_000,
                description="Testing Claude Fable 5.2 for slides",
            )
        )
    homonym = video_record(
        "fablegame01", CHANNELS[3], "Fable 5 game trailer", NOW - timedelta(hours=5), 90_000
    )
    return [*videos, homonym]


async def run(count: int, outliers: int):
    ctx = runable_context()
    llm = ScriptedLLM(script())
    gateway = FakeGateway(early_videos(count, outliers))
    baselines = FakeBaselines({channel: 5_000.0 for channel in CHANNELS})
    outcome = await scanner(ctx, llm, demand_checker(gateway, baselines)).scan(
        ScanInputs(ctx=ctx, bundles=bundles(launch_items() + ipo_items()), now=NOW)
    )
    return ctx, outcome, gateway


async def test_fable_launch_yields_first_mover_draft_tied_to_offering_and_cta():
    ctx, outcome, gateway = await run(count=2, outliers=1)

    assert len(outcome.drafts) == 1
    draft = outcome.drafts[0]
    assert draft.type is RecType.news_opportunity
    subject = draft.subject
    assert subject.key_entity == "Claude Fable 5.2"
    assert subject.conversion_action_id == "act_signup"
    assert {s.publisher for s in subject.reference_sources} == {
        "anthropic.com",
        "techcrunch.com",
        "theverge.com",
    }
    assert subject.reference_sources[0].tier.value == "tier1_primary"
    fit_codes = {reason.code: reason for reason in draft.fit_reasons}
    assert fit_codes["OFFERING_LINK"].params["offering"] == "AI presentations"
    assert fit_codes["VALUE_PROP_LINK"].params["value_prop_id"] == "vp_models"
    assert fit_codes["CTA"].statement == "CTA: Start free"
    assert "BUYER_QUESTION" in fit_codes
    assert draft.confidence.basis is ConfidenceBasis.first_mover
    assert draft.confidence.label is ConfidenceLabel.low
    assert (draft.confidence.outliers, draft.confidence.matching_videos) == (1, 2)
    assert SURFACE_EARLY_SIGNALS in draft.flags
    assert subject.format_suggestion.format == "short"
    assert "TUTORIAL_FOLLOW_UP" in subject.format_suggestion.rationale_codes
    assert subject.urgency == "now"
    assert subject.expires_at == NOW - timedelta(hours=14) + timedelta(days=7)
    assert draft.gate_results[-1].action is GateAction.pass_
    assert [r.code for r in draft.reasons][:3] == ["PRIMARY_SOURCE", "OUTLETS", "FIRST_MOVER"]
    assert all(ref.video_id != "fablegame01" for ref in draft.references)
    assert gateway.searches[0].q == '"Claude Fable 5.2"'

    signal = next(s for s in outcome.signals if s.id == subject.news_signal_id)
    assert signal.status == "drafted"
    assert signal.refresh_eligible
    assert signal.refresh_keywords == ["Claude Fable 5.2"]
    assert signal.credibility.has_primary_source and signal.credibility.independent_outlets == 3


async def test_ipo_story_is_tangential_and_never_a_draft():
    _ctx, outcome, _gateway = await run(count=2, outliers=1)

    ipo = next(s for s in outcome.signals if s.judgement.event_type is EventType.funding_or_ipo)
    assert ipo.entity == "Claude"
    assert ipo.status == "suppressed"
    assert ipo.suppression_reason == "low_relevance"
    assert ipo.judgement.relevance_band is RelevanceBand.tangential


async def test_four_early_videos_make_a_full_card_that_passes_the_reference_gate():
    _ctx, outcome, _gateway = await run(count=4, outliers=2)

    draft = outcome.drafts[0]
    assert SURFACE_RECOMMENDATIONS in draft.flags
    assert len(draft.references) == 4
    assert draft.confidence.basis is ConfidenceBasis.standard
    assert draft.confidence.label is ConfidenceLabel.medium
    assert draft.references[0].is_outlier and draft.references[0].outlier_ratio == 8.0
    assert NewsEvidenceGate().check(draft).action is GateAction.pass_


async def test_strict_reference_gate_drops_first_mover_cards():
    from app.core.config import NewsSettings

    strict = NewsSettings(strict_reference_gate=True)
    ctx = runable_context()
    llm = ScriptedLLM(script())
    gateway = FakeGateway(early_videos(2, 1))
    checker = demand_checker(gateway, FakeBaselines({c: 5_000.0 for c in CHANNELS}), settings=strict)
    outcome = await scanner(ctx, llm, checker, settings=strict).scan(
        ScanInputs(ctx=ctx, bundles=bundles(launch_items()), now=NOW)
    )
    assert outcome.drafts == []
    assert outcome.gate_suppressed[0][1].reason_code == "strict_reference_gate"


async def test_draft_maps_onto_the_recommendation_contract():
    from app.domain.recommendations import (
        ConversionActionRef,
        Lens,
        NewsExtras,
        Recommendation,
        RecommendationExtras,
    )

    ctx, outcome, _gateway = await run(count=4, outliers=2)
    draft = outcome.drafts[0]
    subject = draft.subject
    action = next(a for a in ctx.profile.conversion_actions if a.id == subject.conversion_action_id)
    card = Recommendation(
        id=draft.draft_id,
        type=draft.type,
        summary=f"Short: {subject.key_entity} builds a deck in Runable",
        lens=[Lens.views, Lens.conversion],
        buyer_question="Which AI model makes the best slides?",
        why_it_works=[reason.statement for reason in draft.reasons][:3],
        reference_videos=draft.references,
        fit_to_client="; ".join(reason.statement for reason in draft.fit_reasons)[:200],
        expected_range_vs_baseline=draft.expected_range.display,
        confidence=draft.confidence,
        window_days=draft.window_days,
        generated_at=NOW,
        extras=RecommendationExtras(
            stable_key=draft.stable_key,
            run_id=ctx.client_id,
            rendered_by="template",
            first_seen_at=NOW,
            expires_at=subject.expires_at,
            subject=subject,
            news=NewsExtras(
                reference_sources=subject.reference_sources,
                urgency=subject.urgency,
                format_suggestion=subject.format_suggestion,
                conversion_action=ConversionActionRef(id=action.id, label=action.label, url=action.target),
                compliance=subject.compliance,
            ),
        ),
    )
    strict = card.strict_contract()
    assert strict.type is RecType.news_opportunity and len(strict.reference_videos) == 4
    assert strict.confidence.label is ConfidenceLabel.medium
