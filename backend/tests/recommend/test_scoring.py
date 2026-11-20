import random
from collections import Counter
from dataclasses import replace

import pytest

from app.domain.recommendations import RecType
from app.services.recommend.annotate import DraftAnnotator
from app.services.recommend.evidence.references import ReferenceSelector
from app.services.recommend.inputs import ClientRecommendConfig, FeedbackPenalty
from app.services.recommend.lens import LensAssigner
from app.services.recommend.scoring.diversity import MmrSelector
from app.services.recommend.scoring.scorer import Scorer, tie_break_key
from tests.recommend.conftest import context_for
from tests.recommend.factories import draft, reference
from tests.recommend.fixtures import AS_OF, apollo_inputs, topic_id


@pytest.mark.parametrize("mode", ["both", "views", "conversion"])
def test_lens_mode_selects_the_weight_row(mode: str) -> None:
    ctx = context_for(apollo_inputs(config=ClientRecommendConfig(lens_mode=mode)))
    scored = Scorer().score(draft([reference(index) for index in range(5)]), ctx)

    assert scored.score.weights == ctx.settings.lens_weights[mode]
    assert set(scored.score.components) == {"evidence", "fit", "gap", "intent", "recency"}
    assert 0 < scored.score.total <= 1


def test_without_client_videos_gap_weight_moves_to_evidence_and_intent() -> None:
    inputs = apollo_inputs()
    ctx = context_for(inputs.model_copy(update={"own_channel_id": None}))
    scored = Scorer().score(draft([reference(index) for index in range(5)]), ctx)

    assert "gap" not in scored.score.weights
    assert scored.score.weights["evidence"] == pytest.approx(0.40)
    assert scored.score.weights["intent"] == pytest.approx(0.25)


def test_penalties_multiply_the_total(ctx) -> None:
    base = Scorer().score(draft([reference(index) for index in range(5)]), ctx)
    flagged = Scorer().score(
        draft([reference(index) for index in range(5)], flags=["contradictory", "single_channel"]), ctx
    )

    assert flagged.score.total == pytest.approx(base.score.total * 0.7 * 0.8, rel=1e-4)


def test_dismissed_topics_are_penalised_for_sibling_cards() -> None:
    penalty = FeedbackPenalty(
        group_key=f"topic:{topic_id('bq_knee')}", factor=0.5, until=AS_OF.replace(year=2027)
    )
    ctx = context_for(apollo_inputs(config=ClientRecommendConfig(feedback_penalties=[penalty])))
    scored = Scorer().score(draft([reference(index) for index in range(5)]), ctx)

    assert scored.score.penalties["feedback"] == 0.5


def selector(ctx) -> MmrSelector:
    annotator = DraftAnnotator(ReferenceSelector(), LensAssigner())
    return MmrSelector(
        ctx, ReferenceSelector(), lambda item, refs: Scorer().score(annotator.annotate(item, ctx, refs), ctx)
    )


def test_selection_respects_type_caps(ctx) -> None:
    candidates = [
        Scorer().score(
            draft(
                [reference(10 * key + index, channel=f"c{key}{index}") for index in range(4)], key=str(key)
            ),
            ctx,
        )
        for key in range(8)
    ]
    selection = selector(ctx).select(candidates, 8)

    # only video ideas exist, so the empty slots take the spill allowance and no more
    assert len(selection.selected) == ctx.settings.type_caps["video_idea"] + ctx.settings.type_cap_spill
    assert {reason for _, reason in selection.skipped} == {"type_cap"}


def test_type_caps_hold_while_other_types_can_fill_the_slots(ctx) -> None:
    def scored(key: int, rec_type: RecType):
        refs = [reference(10 * key + index, channel=f"c{key}{index}") for index in range(4)]
        return Scorer().score(draft(refs, rec_type, key=str(key)), ctx)

    ideas = [scored(key, RecType.video_idea) for key in range(6)]
    titles = [scored(10 + key, RecType.title_pattern) for key in range(3)]
    selection = selector(ctx).select([*ideas, *titles], 6)

    counts = Counter(item.type for item in selection.selected)
    assert counts[RecType.video_idea] == ctx.settings.type_caps["video_idea"]
    assert counts[RecType.title_pattern] == ctx.settings.type_caps["title_pattern"]


def test_a_reference_is_used_by_at_most_two_cards_and_swaps_keep_the_minimum(ctx) -> None:
    shared = [reference(index, channel=f"s{index}") for index in range(3)]
    spare = [reference(50 + index, channel=f"p{index}") for index in range(12)]
    candidates = [
        Scorer().score(
            draft([*shared, *spare[4 * key : 4 * key + 4]], RecType.title_pattern, key=f"p{key}"), ctx
        )
        for key in range(3)
    ]
    caps = {**ctx.settings.type_caps, "title_pattern": 3}
    ctx = replace(ctx, settings=ctx.settings.model_copy(update={"type_caps": caps}))
    selection = selector(ctx).select(candidates, 3)

    assert len(selection.selected) == 3
    usage: dict[str, int] = {}
    for item in selection.selected:
        assert len(item.references) >= 3
        for ref in item.references:
            usage[ref.video_id] = usage.get(ref.video_id, 0) + 1

    assert max(usage.values()) <= ctx.settings.max_ref_reuse


def test_ranking_never_depends_on_input_order(ctx) -> None:
    candidates = [
        Scorer().score(
            draft(
                [reference(10 * key + index, channel=f"c{key}{index}") for index in range(3 + key % 3)],
                key=str(key),
            ),
            ctx,
        )
        for key in range(6)
    ]
    shuffled = candidates[:]
    random.Random(7).shuffle(shuffled)

    assert sorted(candidates, key=tie_break_key) == sorted(shuffled, key=tie_break_key)
    first = [item.stable_key for item in selector(ctx).select(candidates, 4).selected]
    second = [item.stable_key for item in selector(ctx).select(shuffled, 4).selected]
    assert first == second


def test_references_keep_every_outlier_before_spreading_channels() -> None:
    same_channel_outliers = [reference(index, channel="manipal") for index in range(3)]
    other_channels = [reference(10 + index, outlier=False) for index in range(4)]
    chosen = ReferenceSelector().select(draft([*same_channel_outliers, *other_channels]))

    assert sum(ref.is_outlier for ref in chosen) == 3
    assert len(chosen) == 5
    assert len({ref.channel_id for ref in chosen}) == 3


def test_references_spread_channels_among_outliers_first() -> None:
    outliers = [reference(0, channel="a"), reference(1, channel="a"), reference(2, channel="b")]
    chosen = ReferenceSelector(display=2).select(draft(outliers))

    assert {ref.channel_id for ref in chosen} == {outliers[0].channel_id, outliers[2].channel_id}
