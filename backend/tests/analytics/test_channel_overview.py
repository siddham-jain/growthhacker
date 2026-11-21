from datetime import date, timedelta

import pytest

from app.domain.common import ChannelRole, VideoFormat
from app.services.analytics.channel_overview import (
    ChannelFacts,
    ChannelInput,
    HistoryPoint,
    OwnerDay,
    channel_landscape,
    channel_overview,
)
from app.services.analytics.settings import RateSettings
from tests.analytics.factories import AS_OF, channel_id, make_video, video_id

RATES = RateSettings()
CID = channel_id(1)


def facts(
    cid: str = CID, title: str = "Clinic", subscribers: int | None = 10_000, **overrides
) -> ChannelFacts:
    values = {
        "channel_id": cid,
        "title": title,
        "published_at": AS_OF - timedelta(days=1000),
        "subscribers": subscribers,
        "total_views": 2_000_000,
        "total_videos": 300,
    }
    return ChannelFacts(**(values | overrides))


def uploads(cid: str = CID) -> list:
    # 3 shorts and 2 long-form in the last 90 days, 2 long-form in the 90 days before, 1 older
    days_ago = [
        (5, VideoFormat.short_form, 2_000, 100),
        (20, VideoFormat.short_form, 1_000, 50),
        (35, VideoFormat.short_form, 3_000, 30),
        (50, VideoFormat.long_form, 10_000, 500),
        (80, VideoFormat.long_form, 4_000, 200),
        (100, VideoFormat.long_form, 9_000, 90),
        (150, VideoFormat.long_form, 9_000, 90),
        (190, VideoFormat.long_form, 9_000, 90),
    ]
    return [
        make_video(
            video_id("v", n), cid, AS_OF - timedelta(days=days), views=views, likes=likes, comments=0, fmt=fmt
        )
        for n, (days, fmt, views, likes) in enumerate(days_ago)
    ]


def channel(**overrides) -> ChannelInput:
    videos = uploads()
    values = {
        "facts": facts(),
        "role": ChannelRole.competitor,
        "videos": videos,
        "oldest_upload_at": min(video.effective_published_at for video in videos),
    }
    return ChannelInput(**(values | overrides))


def test_format_mix_views_and_engagement_over_90_days() -> None:
    overview = channel_overview(channel(), AS_OF, RATES)

    assert overview.format_mix["short_form"] == 3 and overview.format_mix["long_form"] == 2
    assert overview.overall.uploads == 5
    assert overview.overall.total_views == 20_000
    assert overview.overall.mean_views == 4_000
    assert overview.overall.median_views == 3_000
    shorts, long_form = overview.by_format["short_form"], overview.by_format["long_form"]
    assert shorts.mean_views == 2_000 and long_form.mean_views == 7_000
    assert shorts.mean_engagement_rate == pytest.approx((0.05 + 0.05 + 0.01) / 3)
    assert overview.views_share == {"short_form": 0.3, "long_form": 0.7}
    assert overview.views_per_subscriber == pytest.approx(0.4)
    assert [upload.views for upload in overview.top_uploads] == [10_000, 4_000, 3_000]


def test_upload_pace_against_the_previous_window() -> None:
    overview = channel_overview(channel(), AS_OF, RATES)

    assert overview.overall.uploads_per_week == pytest.approx(5 / (90 / 7))
    assert overview.previous_window_uploads == 2
    assert overview.pace_change_pct == pytest.approx(1.5)
    assert overview.days_since_last_upload == pytest.approx(5)
    assert overview.longest_gap_days == pytest.approx(30)


def test_previous_window_is_unknown_when_history_stops_short() -> None:
    overview = channel_overview(channel(oldest_upload_at=AS_OF - timedelta(days=100)), AS_OF, RATES)

    assert overview.previous_window_uploads is None and overview.pace_change_pct is None


def test_growth_from_channel_history() -> None:
    history = [
        HistoryPoint(AS_OF - timedelta(days=200), 8_000, 1_500_000),
        HistoryPoint(AS_OF - timedelta(days=1), 10_000, 2_000_000),
        HistoryPoint(AS_OF - timedelta(days=400), 1_000, 100),
    ]
    overview = channel_overview(channel(history=history), AS_OF, RATES)

    subs = overview.subscriber_growth
    assert subs is not None and subs.source == "channel_history"
    assert (subs.start_value, subs.end_value, subs.change) == (8_000, 10_000, 2_000)
    assert subs.change_pct == pytest.approx(0.25) and subs.days_covered == 199
    assert overview.view_growth is not None and overview.view_growth.change == 500_000


def test_growth_needs_two_days_of_history() -> None:
    history = [HistoryPoint(AS_OF - timedelta(hours=2), 10_000, 2_000_000)]

    assert channel_overview(channel(history=history), AS_OF, RATES).subscriber_growth is None


def test_owner_analytics_growth_wins_over_history() -> None:
    days = [OwnerDay(AS_OF.date() - timedelta(days=n), 1_000, 12, 2) for n in range(3, 368)]
    history = [
        HistoryPoint(AS_OF - timedelta(days=30), 9_000, 1_900_000),
        HistoryPoint(AS_OF - timedelta(days=1), 10_000, 2_000_000),
    ]
    overview = channel_overview(channel(role=ChannelRole.own, owner_days=days, history=history), AS_OF, RATES)

    subs, views = overview.subscriber_growth, overview.view_growth
    assert subs is not None and subs.source == "youtube_analytics"
    assert subs.change == 363 * 10 and subs.start_value == 10_000 - 3_630
    assert subs.end_day == AS_OF.date() - timedelta(days=3) and subs.days_covered == 363
    assert views is not None and views.change == 363_000


def test_hidden_subscribers_leave_ratios_empty() -> None:
    overview = channel_overview(channel(facts=facts(subscribers=None)), AS_OF, RATES)

    assert overview.subscribers is None and overview.views_per_subscriber is None


def test_upcoming_and_scraped_uploads() -> None:
    videos = [
        *uploads(),
        make_video(video_id("u", 1), CID, AS_OF - timedelta(days=1), fmt=VideoFormat.upcoming, views=0),
        make_video(video_id("s", 1), CID, AS_OF - timedelta(days=2), data_source="yt_dlp"),
    ]
    overview = channel_overview(channel(videos=videos), AS_OF, RATES)

    assert overview.format_mix["upcoming"] == 1
    assert overview.overall.uploads == 6
    assert overview.scraped_uploads == 1


def test_landscape_sorts_by_name_and_reports_competitor_medians() -> None:
    other = channel_id(2)
    competitors = [
        channel(facts=facts(other, title="Zeta"), videos=uploads(other)),
        channel(facts=facts(title="alpha", subscribers=20_000)),
    ]
    own = channel(facts=facts(channel_id(3), title="Us"), role=ChannelRole.own, videos=[])
    landscape = channel_landscape(own, competitors, AS_OF, RATES)

    assert [sheet.title for sheet in landscape.competitors] == ["alpha", "Zeta"]
    assert landscape.own is not None and landscape.own.overall.uploads == 0
    assert landscape.competitor_median["mean_views"] == 4_000
    assert landscape.competitor_median["views_per_subscriber"] == pytest.approx(0.3)
    assert landscape.competitor_median["subscriber_growth_pct"] is None
    assert landscape.growth_days == 365 and landscape.window_days == 90


def test_owner_days_outside_the_year_are_ignored() -> None:
    days = [OwnerDay(date(2020, 1, 1), 5, 1, 0)]

    assert channel_overview(channel(owner_days=days), AS_OF, RATES).subscriber_growth is None
