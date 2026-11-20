import uuid
from datetime import timedelta

import pytest

from app.domain.common import SubscriberBand
from app.domain.recommendations import (
    CreatorConsistency,
    CreatorPerformance,
    CreatorRelevance,
    CreatorSubject,
    RecommendationSet,
    RecType,
    make_stable_key,
    recommendation_id,
)
from app.evaluation.backtest.outcomes import FrozenBaselines, collect_outcomes
from app.evaluation.backtest.replay import engine_for, frozen_view
from app.evaluation.backtest.runner import build_config
from app.evaluation.backtest.specs import draft_from_card, system_specs
from app.evaluation.freeze.bundle import BundleStore, LeakageError
from app.evaluation.freeze.canary import run_canary
from app.services.recommend.annotate import match_spec
from app.services.recommend.creators.ranker import CreatorList
from app.services.recommend.gaps import GapReport
from app.services.recommend.service import RecommendOutput, WindowOutput
from tests.analytics.factories import AS_OF
from tests.analytics.test_golden import build_inputs
from tests.conftest import make_settings
from tests.contracts.factories import CLIENT_ID, GENERATED_AT, make_extras, make_recommendation
from tests.evaluation.replay_fixtures import FREEZE_AT, bundle


@pytest.fixture(scope="module")
def frozen():
    return bundle()


@pytest.fixture(scope="module")
def settings():
    return make_settings()


def test_canary_passes_for_the_point_in_time_replay(frozen, settings) -> None:
    engine = engine_for(settings)
    result = run_canary(
        frozen.inputs,
        FREEZE_AT,
        lambda inputs: frozen_view(engine, inputs, frozen, 30, 7).fingerprint(),
        seed=3,
    )
    assert result.passed
    assert result.planted_rows > len(frozen.inputs.videos)


def test_canary_catches_a_replay_that_reads_post_t_rows(frozen) -> None:
    leaky = run_canary(frozen.inputs, FREEZE_AT, lambda inputs: str(len(inputs.videos)), seed=3)
    assert not leaky.passed


def test_frozen_view_sees_only_pre_t_uploads(frozen, settings) -> None:
    view = frozen_view(engine_for(settings), frozen.inputs, frozen, 30, 7)
    assert view.window and all(v.published_at < FREEZE_AT for v in view.window)
    assert all(not v.is_own or v.channel_id == frozen.manifest.own_channel_id for v in view.window)
    assert view.baselines.by_key and all(b.median > 0 for b in view.baselines.by_key.values())


def test_bundle_round_trip_prunes_aged_rows_and_guards_leakage(frozen, tmp_path) -> None:
    store = BundleStore(tmp_path, timedelta(days=28))
    store.write(frozen)
    now = FREEZE_AT + timedelta(days=1)
    loaded = store.read(frozen.manifest.freeze_id, now)
    cutoff = now - timedelta(days=28)
    assert loaded.inputs.videos == [v for v in frozen.inputs.videos if v.metadata_fetched_at >= cutoff]
    kept = [s for items in loaded.inputs.snapshots.values() for s in items]
    assert kept and all(s.captured_at >= cutoff for s in kept)
    assert sum(map(len, frozen.inputs.snapshots.values())) > len(kept)
    assert store.prune(frozen.manifest.freeze_id, now) > 0
    assert store.prune(frozen.manifest.freeze_id, now) == 0
    assert store.stored_ids() == [frozen.manifest.freeze_id]
    with pytest.raises(FileNotFoundError):
        store.read(frozen.manifest.freeze_id, frozen.manifest.expires_at)
    leaky = bundle(build_inputs()).model_copy(update={"inputs": build_inputs()})
    store.write(leaky)
    with pytest.raises(LeakageError):
        store.read(leaky.manifest.freeze_id, now)
    assert store.delete(leaky.manifest.freeze_id) and not store.delete(leaky.manifest.freeze_id)


def test_outcomes_cover_the_horizon_with_honest_statuses(frozen, settings) -> None:
    engine = engine_for(settings)
    view = frozen_view(engine, frozen.inputs, frozen, 30, 7)
    config = build_config(settings, frozen, uuid.uuid4(), "test", view.baselines, True)
    live = build_inputs()
    result = engine.run(live, AS_OF)
    outcomes = collect_outcomes(
        config,
        FrozenBaselines.from_json(config.frozen_baselines),
        live,
        result,
        config.universe,
        config.own_channel_id,
    )
    assert outcomes
    end = FREEZE_AT + timedelta(days=config.horizon_days)
    assert all(FREEZE_AT < o.published_at <= end for o in outcomes)
    assert all(o.channel_id != frozen.manifest.own_channel_id for o in outcomes)
    assert {o.status for o in outcomes} <= {
        "complete",
        "pending",
        "missing_snapshot",
        "deleted",
        "no_baseline",
    }
    for item in outcomes:
        if item.status == "complete":
            assert item.ratio == pytest.approx(item.views_at_age / item.baseline_at_t)
            assert item.is_outlier == (item.ratio >= config.outlier_threshold)
            assert item.views_at_age_method in {"observed", "interpolated", "owner_analytics"}
        else:
            assert item.ratio is None


def output_with(cards, creator_order=()) -> RecommendOutput:
    window = WindowOutput(
        set=RecommendationSet(
            client_id=CLIENT_ID,
            window_days=30,
            as_of=GENERATED_AT,
            generated_at=GENERATED_AT,
            recommendations=cards,
        ),
        creator_list=CreatorList(
            window_days=30,
            sort="matching_uploads",
            groups={
                "strong": [
                    {"channel_id": c, "recommendation_id": r, "group": "strong", "sort_value": 1}
                    for c, r in creator_order
                ]
            }
            if creator_order
            else {},
        ),
        gap_report=GapReport(window_days=30, as_of=GENERATED_AT, rows=[]),
        writer="template",
    )
    return RecommendOutput(
        client_id=CLIENT_ID,
        as_of=GENERATED_AT,
        generated_at=GENERATED_AT,
        selected_window=30,
        windows=[window],
    )


def idea(n: int):
    key = make_stable_key(CLIENT_ID, RecType.video_idea, f"topic:t{n}|long_form", 30)
    return make_recommendation(id=recommendation_id(key), extras=make_extras(key))


def creator(channel_id: str):
    key = make_stable_key(CLIENT_ID, RecType.creator, f"creator:{channel_id}", 30)
    subject = CreatorSubject(
        channel_id=channel_id,
        title="Creator",
        url=f"https://www.youtube.com/channel/{channel_id}",
        subscriber_band=SubscriberBand.from_10k_to_100k,
        uploads_90d=12,
        uploads_checked=20,
        relevance=CreatorRelevance(match_share=0.5, topics_covered=2),
        performance=CreatorPerformance(
            median_outlier_ratio=1.4,
            median_views_at_age=900,
            niche_median_views_at_age=700,
            comparison_age_days=7,
        ),
        consistency=CreatorConsistency(median_gap_days=4, gap_cv=0.5, hit_rate=0.6, last_upload_at=None),
    )
    return make_recommendation(
        type=RecType.creator,
        id=recommendation_id(key),
        extras=make_extras(key, subject=subject, expected_range=None),
        reference_videos=[],
    )


def test_system_specs_follow_display_order_caps_and_the_frozen_universe() -> None:
    allowed, outsider = "UC0InVdvqNyNzKBl1-TL348A", "UCoutsider000000000000AA"
    cards = [idea(1), idea(2), creator(allowed), creator(outsider)]
    result = system_specs(output_with(cards), {"video_idea": 1, "creator": 3}, [allowed])
    assert [s.spec.rec_type for s in result.specs] == [RecType.video_idea, RecType.creator]
    assert result.specs[0].spec.source_ref == cards[0].id
    assert result.specs[1].spec.channel_id == allowed
    assert result.specs[0].confidence == "High"
    assert (result.specs[0].expected_low, result.specs[0].expected_high) == (1.2, 2.0)
    assert any("outside the frozen universe" in note for note in result.notes)
    assert any("rebuilt from cards" in note for note in result.notes)


def test_system_specs_prefer_07s_persisted_match_spec() -> None:
    card = idea(1)
    draft = draft_from_card(card, output_with([card]))
    exact = match_spec(draft).model_copy(update={"topic_ids": ["tr_exact00000"], "specificity": 2})
    stored = {card.extras.stable_key: draft.model_copy(update={"match_spec": exact})}
    result = system_specs(output_with([card]), {"video_idea": 4}, [], stored)
    assert result.specs[0].spec.topic_ids == ["tr_exact00000"]
    assert not any("rebuilt" in note for note in result.notes)


def test_duplicate_specs_count_once() -> None:
    first, second = idea(1), idea(2)
    result = system_specs(output_with([first, second]), {"video_idea": 4}, [])
    assert len(result.specs) == 1
