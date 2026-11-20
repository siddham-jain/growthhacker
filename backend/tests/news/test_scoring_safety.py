from datetime import date, timedelta

import pytest

from app.domain.news import ConfirmationStatus, DemandCheck, EventType, RelevanceBand, SafetyFlag, SourceTier
from app.domain.recommendations import GateAction
from app.services.news.credibility import CredibilityScorer, corroborated
from app.services.news.gates import NewsEvidenceGate
from app.services.news.grounding import GroundingValidator
from app.services.news.history import NewsReactiveHistory, PastNewsEntity
from app.services.news.judge import AliasMap
from app.services.news.ranking import (
    RankedCandidate,
    decay_for,
    expires_at,
    flood_control,
    freshness,
    relevance_score,
    urgency,
)
from app.services.news.safety import SafetyInput, SafetyScreen
from tests.news.fixtures import (
    NOW,
    apollo_context,
    entity,
    judgement,
    news_item,
    runable_context,
    runable_profile,
    video_record,
)
from tests.news.test_golden_runable import run

WATCH = runable_profile().freshness.watch_entities


def grounded(judged, items):
    aliases = AliasMap(
        items={f"n{i}": item for i, item in enumerate(items, 1)},
        offerings={"o1": "off_slides", "o2": "off_sites"},
        value_props={"v1": "vp_models"},
        conversion_actions={"a1": "act_signup"},
    )
    return GroundingValidator([("Claude Mythos 5.2", "Claude Fable 5.2")]).validate(judged, aliases, WATCH)


TC = news_item(
    "https://techcrunch.com/a", "Anthropic releases Claude Fable 5.2", "Claude Fable 5.2 is out now.", NOW
)
VERGE = news_item(
    "https://www.theverge.com/b", "Claude Fable 5.2 hands-on", "We tried Claude Fable 5.2.", NOW
)
PR = news_item(
    "https://www.prnewswire.com/news-releases/fable.html",
    "Fable partner launch",
    "Claude Fable 5.2 partner",
    NOW,
)


def assess(items, ctx=None, **overrides):
    ctx = ctx or runable_context()
    judged = grounded(judgement(entity("Claude Fable 5.2", "n1"), **overrides), items).judgement
    return CredibilityScorer(0.5).assess(items, judged, ctx), judged


def test_credibility_formula():
    assessment, _ = assess([TC, VERGE])
    assert assessment.score == pytest.approx(0.85)
    rumour, _ = assess([TC, VERGE], confirmation=ConfirmationStatus.rumour_or_leak)
    assert rumour.score == pytest.approx(0.425)
    conflicting, _ = assess([TC, VERGE], conflicting=True)
    assert conflicting.score == pytest.approx(0.595)
    press_only, _ = assess([PR])
    assert press_only.score <= 0.5 and "PRESS_RELEASE_ONLY" in press_only.reasons
    assert corroborated(assessment, runable_context(), 2)


def test_single_low_tier_source_fails_and_health_needs_authority():
    blog = news_item("https://someblog.example/x", "Claude Fable 5.2 rumours", "Claude Fable 5.2 soon", NOW)
    weak, judged = assess([blog])
    verdict = CredibilityScorer(0.5).verdict(weak, judged, runable_context())
    assert not verdict.passed and verdict.reason == "low_credibility"
    assert not corroborated(weak, runable_context(), 1)
    health, judged = assess([TC, VERGE], ctx=apollo_context())
    assert CredibilityScorer(0.5).verdict(health, judged, apollo_context()).reason == "health_needs_authority"


def _safety(ctx, items=(TC,), **overrides):
    judged = grounded(judgement(entity("Claude Fable 5.2", "n1"), **overrides), list(items)).judgement
    assessment = CredibilityScorer(0.5).assess(list(items), judged, ctx)
    return SafetyScreen().evaluate(SafetyInput(judged, assessment, list(items), ctx, ["deadly"]))


def test_safety_policies_most_restrictive_wins():
    ctx = runable_context()
    assert _safety(ctx).decision == "allow"
    assert _safety(ctx, flags=(SafetyFlag.tragedy,)).decision == "block"
    assert _safety(ctx, flags=(SafetyFlag.legal,)).decision == "needs_review"
    assert (
        _safety(ctx, flags=(SafetyFlag.political,), event=EventType.policy_change).decision
        == "allow_with_guidance"
    )
    assert _safety(ctx, about_client=True, event=EventType.incident).decision == "needs_review"
    embargo = news_item(
        "https://techcrunch.com/e", "Claude Fable 5.2 under embargo until Friday", "Claude Fable 5.2", NOW
    )
    assert _safety(ctx, items=(embargo,)).decision == "block"
    excluded = runable_profile().freshness.model_copy(update={"excluded_topics": ["Fable"]})
    excluded_ctx = runable_context(profile=runable_profile().model_copy(update={"freshness": excluded}))
    assert _safety(excluded_ctx).decision == "block"


def test_health_policy_blocks_unsourced_claims_and_adds_guidance():
    ctx = apollo_context()
    authority = news_item(
        "https://mohfw.gov.in/a",
        "Health Ministry dengue advisory",
        "deadly dengue cases; Claude Fable 5.2",
        NOW,
    )
    blocked = _safety(ctx, flags=(SafetyFlag.medical_claim,))
    assert blocked.decision == "block" and "HEALTH_CLAIM_WITHOUT_AUTHORITY" in blocked.reasons
    guided = _safety(
        ctx, items=(authority,), flags=(SafetyFlag.death,), event=EventType.public_health_advisory
    )
    assert guided.decision == "allow_with_guidance"
    assert {"GUIDANCE_NO_SENSATIONAL_FRAMING", "GUIDANCE_PREVENTION_FACTS_ONLY"} <= set(guided.reasons)
    assert guided.disclaimers


def test_relevance_decay_urgency_and_expiry():
    judged = grounded(judgement(entity("Claude Fable 5.2", "n1")), [TC]).judgement
    assert relevance_score(judged, WATCH) == 1.0
    foreign = judged.model_copy(update={"region_relevance": "other_region_only"})
    assert relevance_score(foreign, WATCH) == pytest.approx(0.3)
    decay = decay_for(EventType.model_release)
    assert freshness(NOW - timedelta(hours=48), NOW, decay.half_life_hours) == pytest.approx(0.5)
    demand = DemandCheck(
        status="checked",
        entity_query="x",
        published_after=NOW,
        checked_at=NOW,
        early_video_count=0,
        channel_count=0,
        outlier_count=0,
        median_views_per_hour=None,
        saturation="empty",
        quota_units_spent=2,
    )
    assert urgency(0.9, demand, EventType.model_release) == "now"
    assert urgency(0.4, demand, EventType.model_release) == "this_week"
    assert urgency(0.9, demand, EventType.clinical_guideline) == "this_week"
    upcoming = judged.model_copy(
        update={
            "confirmation": ConfirmationStatus.officially_announced_upcoming,
            "event_date": date(2026, 12, 10),
        }
    )
    assert expires_at(upcoming, NOW, decay).date() == date(2026, 12, 13)


def test_flood_control_caps_and_limits_per_family():
    assessment, _ = assess([TC, VERGE])

    def candidate(key, family, band=RelevanceBand.strong, story=None):
        return RankedCandidate(key, family, story or key, band, assessment, 0.9, "empty")

    candidates = [candidate(f"c{i}", "claude fable") for i in range(4)] + [
        candidate("g", "gpt", RelevanceBand.direct)
    ]
    kept, capped = flood_control(candidates, lambda c: c, cap=3)
    assert [c.key for c in kept][0] == "g"
    assert sum(c.family == "claude fable" for c in kept) == 2
    assert len(capped) == 2


def test_own_history_ranges():
    history = NewsReactiveHistory(3, 365)
    videos = [
        video_record(
            f"ownnews{i:04d}",
            "UCrunableOfficial0000000",
            f"Claude Fable {i}.0 launch test",
            NOW - timedelta(days=10 * i),
            1,
        )
        for i in range(1, 5)
    ]
    ratios = {v.video_id: r for v, r in zip(videos, [0.9, 1.2, 1.6, 2.1], strict=True)}
    past = [PastNewsEntity(f"Claude Fable {i}.0", NOW - timedelta(days=10 * i + 1)) for i in range(1, 5)]
    full = history.estimate(runable_context(own_videos=videos, own_day7_ratios=ratios), past, NOW)
    assert full.expected_range.basis == "own" and full.expected_range.n_own == 4
    assert full.expected_range.display.startswith("1.1x–1.7x")
    limited = history.estimate(runable_context(own_videos=videos[:2], own_day7_ratios=ratios), past, NOW)
    assert limited.expected_range.display.startswith(
        "limited history: your news-reaction videos ran 0.9x and 1.2x"
    )
    empty = history.estimate(runable_context(own_videos=[], own_day7_ratios={}), past, NOW)
    assert empty.expected_range.basis == "insufficient"
    no_channel = history.estimate(runable_context(own_channel_id=None), past, NOW)
    assert "no channel connected" in no_channel.expected_range.display


def test_grounding_drops_mismatched_quotes_unknown_ids_and_keeps_alias_display():
    mismatch = judgement(entity("Claude Fable 5.2", "n1", "a quote that is not in the article"))
    assert grounded(mismatch, [TC]).reason == "ungrounded_entity"
    unknown = judgement(entity("Claude Fable 5.2", "n1"), offerings=(("o9", []),), questions=("q7",))
    result = grounded(unknown, [TC])
    assert (
        result.judgement.offering_links == [] and result.judgement.relevance_band is RelevanceBand.tangential
    )
    assert {"offering:o9", "question:q7"} <= set(result.dropped)
    mythos = news_item(
        "https://techcrunch.com/m", "Claude Mythos 5.2 preview", "Claude Mythos 5.2 opens to partners", NOW
    )
    primary = news_item(
        "https://www.anthropic.com/news/fable", "Claude Fable 5.2", "Claude Fable 5.2 ships", NOW
    )
    aliased = grounded(judgement(entity("Claude Mythos 5.2", "n1")), [mythos, primary])
    assert aliased.judgement.key_entity.canonical == "Claude Fable 5.2"
    assert aliased.aliases == ["Claude Mythos 5.2"]
    dashed = news_item("https://techcrunch.com/d", "GPT‑5.1 arrives", "OpenAI GPT‑5.1 is live", NOW)
    assert grounded(judgement(entity("GPT-5.1", "n1")), [dashed]).judgement is not None


async def test_gate_rejects_weak_or_unfit_drafts():
    _ctx, outcome, _gw = await run(2, 1)
    draft = outcome.drafts[0]
    gate = NewsEvidenceGate()
    subject = draft.subject

    def with_subject(**update):
        return draft.model_copy(update={"subject": subject.model_copy(update=update)})

    weak = [s.model_copy(update={"tier": SourceTier.tier3_low}) for s in subject.reference_sources]
    assert gate.check(with_subject(reference_sources=weak)).reason_code == "weak_sources"
    assert gate.check(with_subject(conversion_action_id=None)).reason_code == "no_conversion_action"
    assert gate.check(with_subject(expires_at=draft.as_of)).reason_code == "expired"
    assert gate.check(with_subject(reference_sources=subject.reference_sources[:1])).reason_code == (
        "early_signal_needs_sources"
    )
    no_fit = draft.model_copy(
        update={"fit_reasons": [r for r in draft.fit_reasons if r.code != "OFFERING_LINK"]}
    )
    assert gate.check(no_fit).reason_code == "no_offering_link"
    health = draft.model_copy(update={"flags": [*draft.flags, "health"]})
    assert gate.check(health).reason_code == "health_needs_authority"
    assert gate.check(draft).action is GateAction.pass_
