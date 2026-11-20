import pytest

from app.domain.refresh import ClaimLevel, InsertionAnchor, InsertionKind, InsertionOp, InsertionPosition
from app.models.runs import PipelineRun
from app.services.refresh.ops.apply import apply_ops, verify_additive
from app.services.refresh.ops.merge import RefreshMerger
from app.services.refresh.store import SqlRefreshStore
from app.services.refresh.text import HASHTAG
from tests.foundation.factories import create_tenant, insert_row
from tests.refresh import scenarios
from tests.refresh.fixtures import NOW, engine


def test_verify_uses_the_same_url_span_as_apply() -> None:
    description = (
        "Shop: https://shop.com/https://shop.com/sale-2024\nOld: https://shop.com/https://shop.com/sale\nend"
    )
    old = "https://shop.com/https://shop.com/sale"
    start = description.index("Old: ") + 5
    op = InsertionOp(
        op_id="x",
        kind=InsertionKind.link_fix,
        position=InsertionPosition.before_links_block,
        anchor=InsertionAnchor(
            strategy="url_span",
            anchor_excerpt=description[max(0, start - 80) : start],
            anchor_hash="h",
            char_offset_hint=start,
        ),
        old_text=old,
        text="https://shop.com/sale",
        text_bytes=len("https://shop.com/sale"),
        claim_level=ClaimLevel.l0_navigational,
        phrased_by="template",
    )
    after = apply_ops(description, [op])
    assert after.splitlines()[0] == "Shop: https://shop.com/https://shop.com/sale-2024"
    assert verify_additive(description, after, [op]) == []


def test_devanagari_hashtags_keep_their_vowel_signs() -> None:
    assert HASHTAG.findall("#हिंदी #बालझड़ना #café") == ["हिंदी", "बालझड़ना", "café"]


async def test_one_suggestion_that_cannot_merge_does_not_fail_the_refresh_stage(monkeypatch) -> None:
    baseline = await engine().run(scenarios.runable())
    broken = scenarios.AI_VIDEO
    merge = RefreshMerger.merge

    def failing(self, video, candidates):
        if video.video_id == broken:
            raise ValueError(f"refresh for {broken} is not additive")
        return merge(self, video, candidates)

    monkeypatch.setattr(RefreshMerger, "merge", failing)
    output = await engine().run(scenarios.runable())
    kept = {refresh.video_id for refresh in output.refreshes}
    assert broken not in kept
    assert {refresh.video_id for refresh in baseline.refreshes} - {broken} <= kept
    assert any(item.video_id == broken for item in output.dropped)


@pytest.mark.db
async def test_a_resumed_attempt_rewrites_its_own_refresh_rows(
    session, session_factory, runable_output
) -> None:
    scope = await create_tenant(session, "runable")
    run = await insert_row(session, PipelineRun.__table__, scope)
    store = SqlRefreshStore(session_factory, scope)
    refreshes = [
        refresh.model_copy(update={"client_id": scope.client_id, "run_id": run["id"]})
        for refresh in runable_output.refreshes
    ]
    eligibility = runable_output.eligibility.model_copy(
        update={"client_id": scope.client_id, "run_id": run["id"]}
    )
    await store.save_run(refreshes, eligibility, [], [], NOW)
    await store.save_run(refreshes, eligibility, [], [], NOW)
    history, _, _ = await store.load_history(NOW.replace(year=2025))
    assert sorted(item.refresh.id for item in history) == sorted(refresh.id for refresh in refreshes)
