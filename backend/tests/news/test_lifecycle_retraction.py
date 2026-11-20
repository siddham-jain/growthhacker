from datetime import timedelta

from app.services.news.lifecycle import SignalLifecycle, card_ids
from app.services.news.scanner import ScanInputs
from tests.news.fixtures import (
    NOW,
    RUNABLE_ID,
    FakeBaselines,
    FakeGateway,
    ScriptedLLM,
    bundles,
    demand_checker,
    news_item,
    runable_context,
    scanner,
)
from tests.news.test_golden_runable import CHANNELS, early_videos, launch_items, script

LATER = NOW + timedelta(hours=6)


async def first_scan():
    ctx = runable_context()
    llm = ScriptedLLM(script())
    gateway = FakeGateway(early_videos(2, 1))
    scan = scanner(ctx, llm, demand_checker(gateway, FakeBaselines({c: 5_000.0 for c in CHANNELS})))
    outcome = await scan.scan(ScanInputs(ctx=ctx, bundles=bundles(launch_items()), now=NOW))
    return ctx, llm, scan, outcome


async def rescan(ctx, scan, outcome, extra):
    stored = {j.story_id: j for j in outcome.new_judgements}
    return await scan.scan(
        ScanInputs(
            ctx=ctx,
            bundles=bundles(launch_items() + extra, LATER),
            now=LATER,
            recent_items=launch_items() + extra,
            existing_signals=outcome.signals,
            judgements=stored,
        )
    )


async def test_retraction_by_an_established_outlet_withdraws_the_card_and_the_refresh():
    ctx, llm, scan, first = await first_scan()
    draft = first.drafts[0]
    calls_before = len(llm.calls)
    retraction = news_item(
        "https://www.reuters.com/technology/anthropic-retracts-fable",
        "Anthropic retracts Claude Fable 5.2 announcement",
        "Anthropic said the Claude Fable 5.2 post was published in error and withdrew it.",
        LATER - timedelta(hours=1),
        fetched_at=LATER - timedelta(hours=1),
    )

    second = await rescan(ctx, scan, first, [retraction])

    assert len(llm.calls) == calls_before
    assert second.drafts == []
    signal = next(s for s in second.signals if s.id == draft.subject.news_signal_id)
    assert signal.verification_status == "retracted"
    assert signal.status == "suppressed"
    assert signal.suppression_reason == "retracted_by_source"
    assert not signal.refresh_eligible
    withdrawal = next(w for w in second.withdrawals if w.signal_id == signal.id)
    assert withdrawal.verification_status == "retracted"
    assert draft.draft_id in withdrawal.recommendation_ids
    assert withdrawal.recommendation_ids == card_ids(RUNABLE_ID, signal.id)
    assert withdrawal.refresh_keywords == ["Claude Fable 5.2"]


async def test_low_tier_denial_only_disputes_the_signal():
    ctx, _llm, scan, first = await first_scan()
    rumour = news_item(
        "https://randomtechblog.example/fable-denied",
        "Blogger says Anthropic denies Claude Fable 5.2 benchmarks",
        "",
        LATER - timedelta(hours=1),
        fetched_at=LATER - timedelta(hours=1),
    )

    second = await rescan(ctx, scan, first, [rumour])

    signal = next(s for s in second.signals if s.entity == "Claude Fable 5.2")
    assert signal.verification_status == "disputed"
    assert second.drafts == []
    assert not signal.refresh_eligible
    assert [w.verification_status for w in second.withdrawals] == ["disputed"]


async def test_retracted_signals_stay_retracted():
    ctx, _llm, scan, first = await first_scan()
    retraction = news_item(
        "https://www.reuters.com/technology/anthropic-retracts-fable",
        "Anthropic retracts Claude Fable 5.2 announcement",
        "",
        LATER - timedelta(hours=1),
        fetched_at=LATER - timedelta(hours=1),
    )
    second = await rescan(ctx, scan, first, [retraction])
    third = await rescan(ctx, scan, second, [retraction])
    assert {s.verification_status for s in third.signals if s.entity == "Claude Fable 5.2"} == {"retracted"}


async def test_newer_version_supersedes_and_expiry_withdraws():
    _ctx, _llm, _scan, first = await first_scan()
    older = first.signals[0]
    old_version = older.model_copy(
        update={
            "id": older.id.__class__(int=1),
            "story_id": older.story_id.__class__(int=2),
            "judgement": older.judgement.model_copy(
                update={
                    "key_entity": older.judgement.key_entity.model_copy(
                        update={"canonical": "Claude Fable 5.1"}
                    )
                }
            ),
            "first_seen_at": NOW - timedelta(days=30),
        }
    )
    lifecycle = SignalLifecycle()
    changes = lifecycle.supersessions([old_version, older], [older])
    assert [(c.signal_id, c.verification_status, c.superseded_by) for c in changes] == [
        (old_version.id, "superseded", older.id)
    ]
    expired = lifecycle.expiries([older], older.expires_at + timedelta(seconds=1))
    applied = lifecycle.apply(older, expired[0])
    assert applied.status == "expired" and not applied.refresh_eligible
