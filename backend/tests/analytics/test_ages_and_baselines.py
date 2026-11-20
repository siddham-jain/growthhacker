import math
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.analytics import AgeMethod, ChannelBaseline, OutlierTier
from app.domain.common import FormatSegment, VideoFormat
from app.services.analytics.ages import AgeContext, OwnerDailyViews, ViewsAtAgeResolver
from app.services.analytics.baselines import AgeFamily, AgeValue, BaselineBuilder, VideoFacts
from app.services.analytics.clock import AgeClock
from app.services.analytics.outliers import DualGateRule
from app.services.analytics.segments import DuplicateCandidate, DuplicateCollapser, length_band, segment_of
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.snapshots import CleanSeries, SnapshotCleaner
from tests.analytics.factories import (
    AS_OF,
    channel_id,
    make_snapshot,
    make_video,
    snapshots_at_ages,
    video_id,
)

SETTINGS = AnalyticsEngineSettings()
CHANNEL = channel_id(7)
PUBLISHED = AS_OF - timedelta(days=40)


def clean(points, published=PUBLISHED, as_of=AS_OF) -> CleanSeries:
    snaps = [
        make_snapshot("vid00000001", published + timedelta(days=a), v, published_at=published)
        for a, v in points
    ]
    return SnapshotCleaner(SETTINGS.ages).clean(snaps, published, as_of)


def resolve(points, *, age_now=40.0, owner=None, target=7):
    published = AS_OF - timedelta(days=age_now)
    video = make_video("vid00000001", CHANNEL, published)
    series = clean(points, published)
    ctx = AgeContext(video, series, published, AS_OF, age_now, SETTINGS.ages, owner)
    return ViewsAtAgeResolver(SETTINGS.ages).resolve_age(ctx, target)


def test_suffix_minimum_corrects_spam_filter_decreases():
    series = clean([(1, 100), (2, 150), (3, 140), (4, 160)])
    assert [p.views for p in series.points] == [100, 140, 140, 160]
    assert series.count_decreased


def test_snapshots_before_the_anchor_and_after_as_of_are_dropped():
    series = clean([(-0.5, 5), (1, 100), (50, 999)], as_of=PUBLISHED + timedelta(days=2))
    assert [p.views for p in series.points] == [100]


def test_near_duplicate_snapshots_keep_the_later_one():
    snaps = [
        make_snapshot("vid00000001", PUBLISHED + timedelta(days=1), 100),
        make_snapshot("vid00000001", PUBLISHED + timedelta(days=1, minutes=3), 105),
    ]
    series = SnapshotCleaner(SETTINGS.ages).clean(snaps, PUBLISHED, AS_OF)
    assert [p.views for p in series.points] == [105]


def test_frozen_counts_are_detected_on_young_videos():
    series = clean([(0.5, 10), (1, 500), (2, 500), (3.2, 500), (5, 900)])
    assert series.frozen_intervals
    assert not clean([(1, 500), (2, 600), (3, 700)]).frozen_intervals


def test_live_streams_anchor_on_the_actual_start():
    start = PUBLISHED + timedelta(days=3)
    live = make_video("vid00000002", CHANNEL, PUBLISHED, fmt=VideoFormat.live, live_actual_start_at=start)
    assert AgeClock.anchor(live) == start
    assert AgeClock.anchor(make_video("vid00000003", CHANNEL, PUBLISHED)) == PUBLISHED


def test_observed_within_tolerance():
    value = resolve([(6.8, 950), (10, 1_500)])
    assert (value.method, value.views) == (AgeMethod.observed, 950)
    assert value.label == "views at day 7 (observed)"


def test_interpolation_between_bracketing_snapshots():
    value = resolve([(6.4, 1_000), (7.6, 1_200)])
    assert value.method is AgeMethod.interpolated
    assert value.views == 1_100
    assert value.bracket_spread == pytest.approx(200 / 1_100)


def test_gap_too_wide_falls_to_labelled_proxy():
    value = resolve([(2, 500), (12, 1_400)])
    assert value.method is AgeMethod.age_band_proxy
    assert value.views == 1_400
    assert value.label == "views at 12 days (proxy)"


def test_loose_bracket_is_refused():
    value = resolve([(6.0, 1_000), (8.5, 2_000)])
    assert value.method is AgeMethod.age_band_proxy


def test_young_video_is_pending_and_never_extrapolated():
    value = resolve([(0.5, 300), (1.5, 700)], age_now=2.0)
    assert value.method is AgeMethod.pending
    assert value.views is None
    assert value.label == "too new to judge"


def test_first_seen_after_the_target_age_is_proxy():
    value = resolve([(23, 4_100)], age_now=23.5)
    assert value.method is AgeMethod.age_band_proxy
    assert value.observed_age_days == pytest.approx(23)


def test_owner_analytics_sums_pacific_days_from_the_anchor():
    published = AS_OF - timedelta(days=40)
    first = published.astimezone(ZoneInfo("America/Los_Angeles")).date()
    days = {first + timedelta(days=k): 100 * (k + 1) for k in range(10)}
    owner = OwnerDailyViews(days=days, data_through=first + timedelta(days=20), verified_at=AS_OF)
    value = resolve([(30, 99_999)], owner=owner, target=3)
    assert value.method is AgeMethod.owner_analytics
    assert value.views == 100 + 200 + 300
    assert value.source == "analytics_api"


def test_owner_analytics_waits_for_complete_days():
    owner = OwnerDailyViews(days={}, data_through=date(2020, 1, 1), verified_at=AS_OF)
    assert resolve([(6.9, 10)], owner=owner).method is AgeMethod.observed


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(st.floats(min_value=0.1, max_value=60.0), st.integers(min_value=0, max_value=1_000_000)),
        min_size=1,
        max_size=12,
    ),
    st.sampled_from([1, 3, 7, 30]),
)
def test_resolver_never_extrapolates(points, target):
    value = resolve(points, age_now=61.0, target=target)
    series = clean(points, AS_OF - timedelta(days=61.0))
    if value.method in (AgeMethod.observed, AgeMethod.interpolated):
        later = [p.views for p in series.points if p.age_days >= target - SETTINGS.ages.tol_days(target)]
        assert later and value.views <= max(later)
        earlier = [p.views for p in series.points if p.age_days <= target + SETTINGS.ages.tol_days(target)]
        assert value.views >= min(earlier)


def test_segment_rules_and_bands():
    upcoming = make_video(
        "vid00000004", CHANNEL, PUBLISHED, fmt=VideoFormat.upcoming, live_broadcast_content="upcoming"
    )
    processing = make_video("vid00000005", CHANNEL, PUBLISHED, duration_s=0)
    assert segment_of(upcoming) is None
    assert segment_of(processing) is None
    assert (
        segment_of(make_video("vid00000006", CHANNEL, PUBLISHED, fmt=VideoFormat.short_form, duration_s=45))
        is FormatSegment.short_form
    )
    assert length_band(FormatSegment.short_form, 31) == "0-60s"
    assert length_band(FormatSegment.long_form, 700) == "8-15m"
    assert length_band(FormatSegment.live, 6_945) == ">90m"


def test_apollo_duplicate_pair_collapses_to_one_primary():
    at = PUBLISHED
    pair = [
        DuplicateCandidate("vid0000000a", CHANNEL, "360° Stroke Care | Apollo Hospitals", 31, at, 900),
        DuplicateCandidate("vid0000000b", CHANNEL, "360 Stroke Care | Apollo Hospitals", 31, at, 1_200),
        DuplicateCandidate("vid0000000c", CHANNEL, "Knee replacement explained", 31, at, 5_000),
    ]
    groups = DuplicateCollapser(SETTINGS.segments).collapse(pair)
    assert groups.is_secondary("vid0000000a")
    assert not groups.is_secondary("vid0000000b")
    assert "vid0000000c" not in groups.group_of


def test_cross_channel_reuploads_are_flagged_not_collapsed():
    groups = DuplicateCollapser(SETTINGS.segments).collapse(
        [
            DuplicateCandidate("vid0000000a", CHANNEL, "World Heart Day message", 60, PUBLISHED, 10),
            DuplicateCandidate("vid0000000b", channel_id(8), "World Heart Day message", 61, PUBLISHED, 10),
        ]
    )
    assert set(groups.cross_channel_group_of) == {"vid0000000a", "vid0000000b"}
    assert not groups.group_of


def fact(
    n: int,
    published_days_ago: float,
    value: int | None,
    *,
    segment=FormatSegment.long_form,
    age_now=None,
    proxy_views=None,
    language=None,
    method=AgeMethod.observed,
) -> VideoFacts:
    published = AS_OF - timedelta(days=published_days_ago)
    age_now = published_days_ago if age_now is None else age_now
    views_now = proxy_views if proxy_views is not None else (value or 0) * 3
    series = SnapshotCleaner(SETTINGS.ages).clean(
        snapshots_at_ages(video_id("bl", n), published, [(age_now, views_now)]), published, AS_OF
    )
    values = {(7, AgeFamily.proxy): AgeValue(views_now, AgeMethod.age_band_proxy, age_now, AS_OF)}
    if value is not None:
        values[(7, AgeFamily.data_api)] = AgeValue(value, method, 7.0, AS_OF)
    return VideoFacts(
        video_id=video_id("bl", n),
        channel_id=CHANNEL,
        segment=segment,
        published_at=published,
        language=language,
        baseline_eligible=True,
        series=series,
        views_now=views_now,
        latest_age_days=age_now,
        values=values,
    )


def test_trailing_baseline_uses_only_earlier_videos():
    facts = [fact(i, 100 - i * 2, 1_000 + i) for i in range(12)]
    builder = BaselineBuilder(facts, SETTINGS, AS_OF)
    built = builder.for_video(facts[-1], 7, AgeFamily.data_api).baseline
    assert built.fallback_level == "trailing"
    assert facts[-1].video_id not in built.video_ids
    assert all(f.published_at < facts[-1].published_at for f in facts if f.video_id in built.video_ids)


def test_first_videos_use_the_non_trailing_ladder_step():
    facts = [fact(i, 100 - i * 2, 1_000) for i in range(8)]
    built = BaselineBuilder(facts, SETTINGS, AS_OF).for_video(facts[0], 7, AgeFamily.data_api).baseline
    assert built.fallback_level == "non_trailing"
    assert built.sufficient


def test_too_few_videos_is_insufficient_and_never_crosses_segments():
    long_form = [fact(i, 50 - i, 1_000) for i in range(3)]
    shorts = [fact(10 + i, 50 - i, 1_000, segment=FormatSegment.short_form) for i in range(10)]
    built = BaselineBuilder(long_form + shorts, SETTINGS, AS_OF).for_video(
        long_form[0], 7, AgeFamily.data_api
    )
    assert built.baseline.fallback_level == "none"
    assert not built.baseline.sufficient
    assert all(v.startswith("bl") and int(v[2:]) < 10 for v in built.baseline.video_ids)


def test_language_split_when_groups_differ_twofold():
    hindi = [fact(i, 80 - i, 5_000, language="hi") for i in range(6)]
    english = [fact(20 + i, 79.5 - i, 1_000, language="en") for i in range(6)]
    target = fact(40, 1, 1_000, language="en")
    built = BaselineBuilder(hindi + english + [target], SETTINGS, AS_OF).for_video(
        target, 7, AgeFamily.data_api
    )
    assert built.baseline.median == 1_000


def test_proxy_peers_follow_the_age_band_and_widen():
    peers = [fact(i, 20 + i, None, proxy_views=1_000) for i in range(6)]
    far = [fact(30 + i, 200 + i, None, proxy_views=50_000) for i in range(6)]
    target = fact(99, 24, None, proxy_views=4_000)
    built = BaselineBuilder(peers + far + [target], SETTINGS, AS_OF).for_video(target, 7, AgeFamily.proxy)
    low, high = built.baseline.age_band
    assert low == pytest.approx(24 / 1.5) and high == pytest.approx(24 * 1.5)
    assert built.baseline.median == 1_000
    assert built.baseline.method is AgeMethod.age_band_proxy

    sparse = [fact(i, age, None, proxy_views=1_000) for i, age in enumerate((20, 40, 50, 55, 60))]
    widened = BaselineBuilder(sparse + [target], SETTINGS, AS_OF).for_video(target, 7, AgeFamily.proxy)
    assert widened.widened and widened.baseline.fallback_level == "widened"


def test_proxy_peer_with_bracketing_snapshots_is_read_at_the_exact_age():
    published = AS_OF - timedelta(days=60)
    series = SnapshotCleaner(SETTINGS.ages).clean(
        snapshots_at_ages(video_id("bl", 50), published, [(20, 800), (25, 1_300), (60, 9_000)]),
        published,
        AS_OF,
    )
    tracked = VideoFacts(
        video_id=video_id("bl", 50),
        channel_id=CHANNEL,
        segment=FormatSegment.long_form,
        published_at=published,
        language=None,
        baseline_eligible=True,
        series=series,
        views_now=9_000,
        latest_age_days=60.0,
    )
    others = [fact(i, 22 + i, None, proxy_views=1_000) for i in range(5)]
    target = fact(99, 22.5, None, proxy_views=3_000)
    built = BaselineBuilder([tracked, *others, target], SETTINGS, AS_OF).for_video(target, 7, AgeFamily.proxy)
    assert tracked.video_id in built.baseline.video_ids


def test_proxy_peers_are_capped_at_the_nearest_thirty():
    peers = [fact(i, 10 + i * 0.1, None, proxy_views=1_000) for i in range(45)]
    target = fact(99, 12, None, proxy_views=1_000)
    built = BaselineBuilder([*peers, target], SETTINGS, AS_OF).for_video(target, 7, AgeFamily.proxy)
    assert built.baseline.n == 30


def baseline(median: float, mad: float, sufficient: bool = True) -> ChannelBaseline:
    return ChannelBaseline(
        baseline_id="bl_x",
        channel_id=CHANNEL,
        segment=FormatSegment.long_form,
        metric="views_at_age",
        age_days=7,
        method=AgeMethod.observed,
        as_of=AS_OF,
        median=median,
        mean=median,
        trimmed_mean_10=median,
        p25=median,
        p75=median,
        mad_log10=mad,
        n=10,
        video_ids=[],
        fallback_level="trailing",
        sufficient=sufficient,
    )


@pytest.mark.parametrize(
    ("value", "median", "mad", "tier"),
    [
        (6_000, 1_000, 0.1, OutlierTier.strong_outlier),
        (3_600, 1_000, 0.1, OutlierTier.outlier),
        (3_600, 1_000, 0.5, OutlierTier.breakout),
        (1_000, 1_000, 0.1, OutlierTier.normal),
        (500, 1_000, 0.1, OutlierTier.below),
        (40, 12, 0.01, OutlierTier.breakout),
    ],
)
def test_dual_gate_rule(value, median, mad, tier):
    rule = DualGateRule(
        SETTINGS.outliers, SETTINGS.baselines.min_baseline_views, SETTINGS.baselines.mad_floor
    )
    assert rule.assess(value, baseline(median, mad))[0] is tier


def test_floor_and_mad_floor_guard_tiny_channels():
    rule = DualGateRule(
        SETTINGS.outliers, SETTINGS.baselines.min_baseline_views, SETTINGS.baselines.mad_floor
    )
    tier, ratio, z = rule.assess(30, baseline(0, 0.0))
    assert ratio == pytest.approx(3.0)
    assert z == pytest.approx(math.log10(30) / 0.05)
    assert tier is OutlierTier.breakout
    assert rule.assess(5_000, baseline(1_000, 0.1, sufficient=False))[0] is OutlierTier.insufficient
