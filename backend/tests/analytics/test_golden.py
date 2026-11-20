"""integration over the apollo-like channel at t0 plus three tracked competitors."""

from datetime import date, timedelta

import pytest

from app.domain.analytics import AgeMethod, ChannelScorecard, LiftScope, OutlierTier
from app.domain.common import FormatSegment
from app.domain.recommendations import DATA_DISCLOSURE
from app.domain.topics import TopicKind
from app.domain.youtube import (
    OAuthConnectionStatus,
    OwnAnalytics,
    OwnVideoDayViews,
    OwnVideoMetrics,
    SearchTermRow,
    TrafficSourceRow,
)
from app.services.analytics.baselines import summarise
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.settings import AnalyticsEngineSettings
from tests.analytics.apollo_fixture import (
    APOLLO,
    COMPETITORS,
    DUPLICATES,
    LIVE,
    ORGANIC,
    SPIKES,
    UPCOMING,
    apollo_channel,
    apollo_vid,
    apollo_videos_and_snapshots,
    competitor_channels,
    competitor_vid,
    competitor_videos_and_snapshots,
    is_competitor_outlier,
)
from tests.analytics.factories import AS_OF, make_comment, make_inputs, make_match, make_topic_ref

KNEE = make_topic_ref(1, "knee replacement recovery")
COST_QUESTION = make_topic_ref(
    2, "knee replacement cost", TopicKind.buyer_question, ["knee replacement", "cost"]
)


def build_inputs(*, own_analytics=None, extra_snapshots=None):
    apollo_videos, apollo_snaps = apollo_videos_and_snapshots()
    rival_videos, rival_snaps = competitor_videos_and_snapshots()
    snapshots = {**apollo_snaps, **rival_snaps}
    for vid, items in (extra_snapshots or {}).items():
        snapshots[vid] = snapshots.get(vid, []) + items
    matches = [make_match(v.video_id, KNEE) for v in rival_videos if "recover" in v.title]
    matches += [make_match(v.video_id, KNEE, score=0.6) for v in rival_videos[:6]]
    comments = {
        competitor_vid(2, 4): [
            make_comment(competitor_vid(2, 4), i, t)
            for i, t in enumerate(
                ["What is the knee replacement cost?", "Where is your branch?", "Which doctor operated?"] * 4
            )
        ]
    }
    return make_inputs(
        [apollo_channel(), *competitor_channels()],
        apollo_videos + rival_videos,
        snapshots=snapshots,
        topic_refs=[KNEE, COST_QUESTION],
        topic_matches=matches,
        comments=comments,
        own_analytics=own_analytics,
    )


@pytest.fixture(scope="module")
def result():
    return AnalyticsEngine(AnalyticsEngineSettings()).run(build_inputs(), AS_OF)


def test_apollo_median_is_the_baseline_not_the_mean(result):
    views = [m.views_now for m in result.metrics[7].values() if m.channel_id == APOLLO]
    summary = summarise(views)
    assert len(views) == 48
    assert summary.median == pytest.approx(1_320)
    assert 50_000 < summary.mean < 53_000


def test_upcoming_items_are_excluded_from_everything(result):
    for index in UPCOMING:
        assert result.excluded[apollo_vid(index)] == "upcoming"
        assert apollo_vid(index) not in result.metrics[7]


def test_promoted_spikes_are_suppressed_and_the_organic_outlier_survives(result):
    metrics = result.metrics[7]
    for index in SPIKES:
        outlier = metrics[apollo_vid(index)].outlier
        assert outlier.tier in (OutlierTier.outlier, OutlierTier.strong_outlier)
        assert outlier.suppressed_reason == "views_without_engagement"
        assert not outlier.is_outlier
        assert "views_without_engagement" in metrics[apollo_vid(index)].flags
    organic = metrics[apollo_vid(ORGANIC)].outlier
    assert organic.is_outlier and organic.method is AgeMethod.age_band_proxy
    assert organic.ratio > 20
    window = result.windows[90]
    assert [r.video_id for r in window.outliers["own"]] == [apollo_vid(ORGANIC)]
    assert {r.video_id for r in window.unusual_spikes["own"]} == {apollo_vid(i) for i in SPIKES}


def test_apollo_at_onboarding_is_compared_by_labelled_proxy(result):
    window = result.windows[30]
    assert window.same_age_mode["own"] == "proxy"
    record = window.outliers["own"][0] if window.outliers["own"] else None
    assert record is None or record.views_at_age.method is AgeMethod.age_band_proxy
    assert result.windows[90].outliers["own"][0].views_at_age.label.endswith("(proxy)")


def test_live_replays_and_short_form_stay_in_their_own_segments(result):
    metrics = result.metrics[7]
    for index in LIVE:
        live = metrics[apollo_vid(index)]
        assert live.segment is FormatSegment.live
        assert live.outlier.tier is OutlierTier.insufficient
    assert metrics[apollo_vid(DUPLICATES[0])].segment is FormatSegment.short_form
    ids = {apollo_vid(i) for i in DUPLICATES}
    groups = {metrics[v].duplicate_group_id for v in ids}
    assert len(groups) == 1 and None not in groups
    assert sum("duplicate_of" in metrics[v].flags for v in ids) == 1


def test_tracked_competitors_switch_to_true_same_age_values(result):
    window = result.windows[30]
    assert window.same_age_mode["competitor_set"] == "true"
    competitor_outliers = window.outliers["competitor_set"]
    assert competitor_outliers
    assert all(
        r.views_at_age.method in (AgeMethod.observed, AgeMethod.interpolated) for r in competitor_outliers
    )
    assert all(r.views_at_age.age_days == 7 for r in competitor_outliers)
    assert {r.video_id for r in competitor_outliers} == {
        competitor_vid(i, n)
        for i in (2, 3, 4)
        for n in range(15)
        if is_competitor_outlier(n)
        and (AS_OF - timedelta(days=1 + n * 2 + i * 0.1)) >= AS_OF - timedelta(days=30)
        and 1 + n * 2 + i * 0.1 >= 7.5
    }


def test_seven_day_window_compares_at_day_three(result):
    window = result.windows[7]
    assert window.comparison_age_days == 3
    assert result.windows[90].secondary_age_days == 30
    assert set(result.metrics) == {3, 7, 30}


def test_how_to_pattern_is_supported_over_ninety_days(result):
    lifts = {
        (x.trait_key, x.trait_value): x
        for x in result.windows[90].lifts["competitor_set"]
        if x.interaction is None
    }
    how_to = lifts[("title.is_how_to", "true")]
    assert how_to.status in ("supported", "redundant")
    assert how_to.outlier_channels_with == 3
    assert how_to.comparison_method in (AgeMethod.observed, AgeMethod.interpolated)
    assert how_to.lift_ci[0] > 1


def test_scorecards_are_evidence_sheets_without_scores(result):
    fields = set(ChannelScorecard.model_fields)
    assert not {"score", "rank", "size_vs_client"} & fields
    cards = {c.channel_id: c for c in result.windows[30].scorecards}
    assert set(cards) == set(COMPETITORS)
    card = cards[COMPETITORS[0]]
    assert card.uploads_in_window == 15
    assert card.segment_mix == {FormatSegment.long_form: 1.0}
    assert card.median_views_at_age[FormatSegment.long_form].method in (
        AgeMethod.observed,
        AgeMethod.interpolated,
    )
    outliers, eligible = card.outlier_hit_rate
    assert outliers == 2 and eligible > 0
    assert card.paid_placement_share == (1, 15)
    assert card.topic_coverage[KNEE.id].n_videos >= 1


def test_gap_matrix_counts_topic_coverage_per_channel(result):
    cells = result.windows[90].gap_matrix.cells
    assert set(cells) == {KNEE.id, COST_QUESTION.id}
    assert cells[KNEE.id][APOLLO].n_videos == 0
    assert all(cells[KNEE.id][cid].n_outliers >= 1 for cid in COMPETITORS)
    assert all(cell.n_videos == 0 for cell in cells[COST_QUESTION.id].values())


def test_conversion_lens_is_a_separate_block(result):
    lens = result.windows[90].conversion
    assert lens.description_cta_coverage[f"{APOLLO}:has_booking_cta"][0] > 0
    signals = lens.comment_intent[competitor_vid(2, 4)]
    assert signals.sufficient and signals.matched_buyer_questions == [(COST_QUESTION.id, 4)]
    assert lens.own_channel is None


def test_trending_uses_niche_scope_and_caps_per_channel(result):
    trending = result.windows[90].trending
    assert trending.items
    per_channel = {}
    for item in trending.items:
        per_channel[item.channel_id] = per_channel.get(item.channel_id, 0) + 1
    assert max(per_channel.values()) <= 2
    assert all(KNEE.id in item.topic_ref_ids for item in trending.items)


def test_every_window_carries_the_disclosure(result):
    assert result.disclosure == DATA_DISCLOSURE
    assert set(result.windows) == {7, 30, 90}
    assert all(w.disclosure == DATA_DISCLOSURE for w in result.windows.values())


def test_results_are_deterministic_under_shuffled_inputs(result):
    inputs = build_inputs()
    shuffled = inputs.model_copy(
        update={
            "videos": list(reversed(inputs.videos)),
            "snapshots": {k: list(reversed(v)) for k, v in reversed(list(inputs.snapshots.items()))},
            "topic_matches": list(reversed(inputs.topic_matches)),
        }
    )
    again = AnalyticsEngine(AnalyticsEngineSettings()).run(shuffled, AS_OF)
    assert again.content_hash() == result.content_hash()


def test_future_snapshots_cannot_leak_into_a_frozen_run(result):
    vid = competitor_vid(2, 4)
    from tests.analytics.factories import make_snapshot

    future = {vid: [make_snapshot(vid, AS_OF + timedelta(days=3), 10_000_000, 1, 1)]}
    leaked = AnalyticsEngine(AnalyticsEngineSettings()).run(build_inputs(extra_snapshots=future), AS_OF)
    assert leaked.content_hash() == result.content_hash()


def test_retention_expiry_reverts_older_videos_to_the_proxy(result):
    inputs = build_inputs()
    cutoff = AS_OF - timedelta(days=28)
    retained = inputs.model_copy(
        update={
            "snapshots": {k: [s for s in v if s.captured_at >= cutoff] for k, v in inputs.snapshots.items()}
        }
    )
    expired = AnalyticsEngine(AnalyticsEngineSettings()).run(retained, AS_OF)
    old = competitor_vid(2, 30)
    assert result.metrics[7][old].views_at_age[7].method is AgeMethod.observed
    assert expired.metrics[7][old].views_at_age[7].method is AgeMethod.age_band_proxy
    assert expired.windows[90].same_age_mode["competitor_set"] in ("proxy", "mixed_by_video")


def test_owner_analytics_give_exact_values_and_a_separate_conversion_block():
    vid = apollo_vid(ORGANIC)
    first = date(2026, 11, 1)
    analytics = OwnAnalytics(
        connection=OAuthConnectionStatus(
            status="active", channel_id=APOLLO, scopes=["yt-analytics.readonly"], last_verified_at=AS_OF
        ),
        data_through=AS_OF.date(),
        video_daily_views=[
            OwnVideoDayViews(video_id=vid, day=first + timedelta(days=k), views=2_000) for k in range(20)
        ],
        video_period=[
            OwnVideoMetrics(
                video_id=vid,
                period_start=first,
                period_end=AS_OF.date(),
                views=40_000,
                estimated_minutes_watched=9_000,
                average_view_duration_s=120.0,
                average_view_percentage=40.0,
                subscribers_gained=120,
                subscribers_lost=4,
                likes=2_000,
                comments=40,
                shares=30,
            )
        ],
        traffic_sources=[
            TrafficSourceRow(
                video_id=vid,
                period_start=first,
                period_end=AS_OF.date(),
                source_type=t,
                views=n,
                estimated_minutes_watched=1,
            )
            for t, n in (("YT_SEARCH", 30_000), ("SUGGESTED", 10_000))
        ],
        search_terms=[
            SearchTermRow(
                video_id=vid,
                period_start=first,
                period_end=AS_OF.date(),
                term="heart attack signs",
                views=9_000,
                estimated_minutes_watched=1,
            )
        ],
    )
    owned = AnalyticsEngine(AnalyticsEngineSettings()).run(build_inputs(own_analytics=analytics), AS_OF)
    value = owned.metrics[7][vid].views_at_age[7]
    assert value.method is AgeMethod.owner_analytics and value.source == "analytics_api"
    report = owned.windows[90].own_report
    own = report.conversion.own_channel
    assert own.subscribers_gained_by_video[vid] == 120
    assert own.subscribers_gained_per_1k_views[vid] == pytest.approx(3.0)
    assert own.traffic_source_share[vid]["YT_SEARCH"] == pytest.approx(0.75)
    assert own.top_search_terms[vid] == [("heart attack signs", 9_000)]
    assert report.conversion.description_cta_coverage["has_booking_cta"][0] > 0


def test_own_report_summarises_the_window(result):
    report = result.windows[90].own_report
    assert report.channel_id == APOLLO and report.comparison_age_days == 7
    assert apollo_vid(ORGANIC) == report.best[0]
    assert report.method_mix == {
        AgeMethod.age_band_proxy: len([v for v in report.videos if v.method is AgeMethod.age_band_proxy])
    }
    assert report.consistency.uploads_per_week > 0
    assert report.by_trait["title.is_question=true"].n >= 1
    assert report.by_format["long_form"].n >= 3
    assert all(b.channel_id == APOLLO for b in report.baselines)
    assert result.windows[90].scorecards and LiftScope.own.value in result.windows[90].lifts


def test_proxy_agreement_and_weighted_scores_for_evaluation():
    from app.services.analytics.state import AssessmentBook, StateBuilder
    from app.services.analytics.validation import proxy_agreement, weighted_precision_recall

    settings = AnalyticsEngineSettings()
    states, _ = StateBuilder(settings, AS_OF).build(build_inputs())
    book = AssessmentBook(states.values(), settings, AS_OF)
    report = proxy_agreement(book, [s for s in states.values() if s.channel_id in COMPETITORS])
    assert report and all(r.segment is FormatSegment.long_form and r.n > 0 for r in report)
    assert all(r.kappa is None or -1 <= r.kappa <= 1 for r in report)

    score = weighted_precision_recall(
        [True, True, False, False], [True, False, True, False], [1.0, 1.0, 3.0, 1.0]
    )
    assert score.precision == pytest.approx(0.5) and score.recall == pytest.approx(0.25)
    with pytest.raises(ValueError):
        weighted_precision_recall([True], [], [])


def test_proxy_and_true_values_are_never_mixed_in_one_ratio(result):
    from app.services.analytics.baselines import family_of

    baselines = {b.baseline_id: b for b in result.baselines}
    checked = 0
    for by_video in result.metrics.values():
        for metrics in by_video.values():
            if metrics.outlier is None:
                continue
            baseline = baselines[metrics.outlier.baseline_id]
            assert family_of(metrics.outlier.method) == family_of(baseline.method)
            checked += 1
    assert checked > 100
    for window in result.windows.values():
        for lifts in window.lifts.values():
            assert len({family_of(lift.comparison_method) for lift in lifts}) <= 1
