from typing import Any

from app.domain.recommendations import RecType
from app.domain.refresh import ClaimLevel, InsertionKind, InsertionPosition
from app.ports.llm import LLMResult, TaskId, TokenUsage
from app.services.refresh.ops.apply import apply_ops, verify_additive
from app.services.refresh.policy.domain import HealthcarePolicy
from app.services.refresh.structure import parse_description
from app.services.refresh.triggers.base import TriggerKind
from tests.refresh import scenarios
from tests.refresh.fixtures import engine, news_signal, topic_id

L1_LINE = "Update (Nov 2026): Claude Fable 5.2 is now available. This video was recorded before its release."
L3_LINE = "Update (Nov 2026): Claude Fable 5.2 is now available — the workflow shown here applies to it."


def _refresh_for(output, video_id):
    return next(refresh for refresh in output.refreshes if refresh.video_id == video_id)


def _news_op(refresh):
    news_ids = {trigger.id for trigger in refresh.triggers if trigger.kind == TriggerKind.news_entity}
    return next(op for op in refresh.operations if set(op.trigger_ids) & news_ids)


async def test_runable_fable_insertion_is_one_honest_line_above_the_fold() -> None:
    data = scenarios.runable()
    output = await engine().run(data)
    assert output.eligibility.decision in ("periodic", "active")
    refresh = _refresh_for(output, scenarios.AI_VIDEO)
    op = _news_op(refresh)
    assert op.text == L1_LINE
    assert op.kind is InsertionKind.insert
    assert op.claim_level is ClaimLevel.l1_context
    assert op.keywords_added == [scenarios.FABLE]
    description = data.own_videos[0].description
    after = apply_ops(description, [op])
    assert verify_additive(description, after, [op]) == []
    assert after.index(scenarios.FABLE) < 150
    assert after.splitlines()[1] == L1_LINE
    assert refresh.review_required
    assert refresh.resulting_bytes <= 5000
    assert refresh.confidence.label.value == "Medium"
    assert (refresh.confidence.outliers, refresh.confidence.channels) == (4, 3)
    draft = next(item for item in output.recommendation_drafts if item.subject.video_id == scenarios.AI_VIDEO)
    assert draft.type is RecType.description_refresh
    assert len(draft.references) == 4
    assert {result.gate for result in draft.gate_results} == {"AdditiveDiffOnly", "MinimalEdit"}
    assert "We can't predict a number" in refresh.expected_effect


async def test_runable_owner_confirmation_unlocks_applicability_wording() -> None:
    output = await engine().run(scenarios.runable(confirmed=True))
    op = _news_op(_refresh_for(output, scenarios.AI_VIDEO))
    assert op.text == L3_LINE
    assert op.claim_level is ClaimLevel.l3_applicability


async def test_runable_without_enough_references_shows_thin_data_instead() -> None:
    output = await engine().run(scenarios.runable(refs=1))
    assert not output.recommendation_drafts
    assert any(
        "Only 1 recent video in the last 30 days uses 'Claude Fable 5.2'" in m for m in output.messages
    )
    assert any(item.reason.startswith("fewer than 3 reference videos") for item in output.dropped)


async def test_newest_release_wins_over_an_older_one() -> None:
    older = news_signal(
        "Claude Fable 5.1",
        topic_ref_ids=[topic_id("AI tools")],
        event_days_ago=3,
        first_seen_days_ago=3,
    )
    data = scenarios.runable(extra_signals=(older,))
    data = data.model_copy(
        update={
            "reference_pool": data.reference_pool
            + [
                item.model_copy(
                    update={
                        "video": item.video.model_copy(
                            update={
                                "video_id": f"old{index:08d}",
                                "title": f"Claude Fable 5.1 review {index}",
                            }
                        )
                    }
                )
                for index, item in enumerate(data.reference_pool)
            ]
        }
    )
    output = await engine().run(data)
    refresh = _refresh_for(output, scenarios.AI_VIDEO)
    assert _news_op(refresh).text == L1_LINE
    assert all("5.1" not in op.text for op in refresh.operations)


async def test_apollo_hygiene_finds_booking_chapters_and_malformed_link() -> None:
    data = scenarios.apollo()
    output = await engine().run(data)
    hygiene = output.hygiene
    assert (hygiene.videos_checked, hygiene.with_booking_link) == (50, 2)
    assert (hygiene.long_videos, hygiene.with_valid_chapters) == (50, 0)
    assert hygiene.malformed_links == 1
    assert "2 of 50 recent descriptions link to your booking page" in hygiene.summary
    assert output.eligibility.decision == "hygiene_only"
    assert not output.recommendation_drafts
    assert len(output.refreshes) == 10
    fix = _refresh_for(output, scenarios.MALFORMED_VIDEO)
    link_fix = next(op for op in fix.operations if op.kind is InsertionKind.link_fix)
    assert link_fix.old_text == scenarios.MALFORMED_FACEBOOK
    assert link_fix.text == "https://www.facebook.com/TheApolloHospitals/"
    policy = HealthcarePolicy()
    videos = {item.video_id: item for item in data.own_videos}
    for refresh in output.refreshes:
        assert refresh.review_required
        assert refresh.confidence.basis.value == "rule_based"
        assert refresh.id in hygiene.refresh_ids
        assert any(advisory.kind == "chapters_needed" for advisory in refresh.advisories)
        description = videos[refresh.video_id].description
        after = apply_ops(description, refresh.operations)
        assert verify_additive(description, after, refresh.operations) == []
        disclaimer_line = parse_description(after).disclaimer_line
        for op in refresh.operations:
            assert policy.violations(op.text, description) == []
            if op.kind is InsertionKind.insert and op.position is not InsertionPosition.append:
                assert after.splitlines().index(op.text) < disclaimer_line
    assert scenarios.CSR_VIDEO not in {refresh.video_id for refresh in output.refreshes} or not any(
        op.kind is InsertionKind.insert for op in _refresh_for(output, scenarios.CSR_VIDEO).operations
    )
    assert any("medical/compliance team" in message for message in output.messages)


async def test_apollo_booking_link_follows_the_location_in_the_description() -> None:
    output = await engine().run(scenarios.apollo())
    ctas = [
        (refresh.video_id, op.text)
        for refresh in output.refreshes
        for op in refresh.operations
        if op.kind is InsertionKind.insert and "apollohospitals.com" in op.text
    ]
    assert ctas
    for vid, text in ctas:
        index = int(vid[-5:])
        expected = scenarios.HYDERABAD_BOOKING if index % 2 else scenarios.BOOKING
        assert text.endswith(expected)
        assert "best" not in text.casefold()


async def test_dental_clinic_needs_no_refresh() -> None:
    output = await engine().run(scenarios.dental())
    assert output.eligibility.decision == "hygiene_only"
    assert output.needs_refresh is False
    assert output.refreshes == []
    assert output.headline.startswith("No refresh needed")
    assert output.keyword_checks_ran is False


class FakeLLM:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    async def generate_structured(self, *, task: TaskId, schema: Any, **_: Any) -> LLMResult:
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return LLMResult(
            value=schema(text=text),
            raw_text=text,
            task=task,
            prompt_id="refresh_phrasing",
            prompt_version="1",
            provider="fake",
            model="fake",
            route_index=0,
            attempts=1,
            repaired=False,
            usage=TokenUsage(),
            cost_usd=0.0,
            latency_ms=1,
            stop_reason="end_turn",
        )


async def test_llm_rewrites_and_medical_claims_fall_back_to_the_template() -> None:
    rewrite = "In this video I share my exact workflow with Claude Fable 5.2: 7 ways to make money with AI tools in 2026."
    claim = "Update (Nov 2026): Claude Fable 5.2 is guaranteed to cure your income problems."
    llm = FakeLLM([rewrite, claim])
    output = await engine(llm=llm).run(scenarios.runable())
    op = _news_op(_refresh_for(output, scenarios.AI_VIDEO))
    assert llm.calls == 2
    assert op.text == L1_LINE
    assert op.phrased_by == "template"
    assert output.counts["llm_fallbacks"] >= 1


async def test_valid_llm_variation_is_used() -> None:
    varied = (
        "Update (Nov 2026): Claude Fable 5.2 is now available; this video was recorded before its release."
    )
    output = await engine(llm=FakeLLM([varied])).run(scenarios.runable())
    op = _news_op(_refresh_for(output, scenarios.AI_VIDEO))
    assert op.text == varied
    assert op.phrased_by == "llm"
