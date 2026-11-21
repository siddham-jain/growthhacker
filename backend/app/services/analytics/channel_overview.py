"""founder-facing channel overview: format mix, views, engagement, pace and growth from public counts.

everything is simple maths on youtube data (counts, sums, means, medians, ratios) for one channel at a
time; the landscape puts channels side by side with a competitor median and never ranks them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.analytics import ChannelLandscape, ChannelOverview, FormatStats, GrowthFigure, TopUpload
from app.domain.common import ChannelRole, VideoFormat
from app.domain.youtube import VideoRecord
from app.services.analytics.patterns.stats import median
from app.services.analytics.rates import compute_rates
from app.services.analytics.reports.common import gaps_in_days
from app.services.analytics.settings import RateSettings

WINDOW_DAYS = 90
GROWTH_DAYS = 365
TOP_UPLOADS = 3
# formats with settled public counts; upcoming and still-live (unknown) uploads are only counted in the mix
STAT_FORMATS = (VideoFormat.short_form, VideoFormat.long_form, VideoFormat.live)


@dataclass(frozen=True)
class ChannelFacts:
    channel_id: str
    title: str
    published_at: datetime | None
    subscribers: int | None
    total_views: int | None
    total_videos: int | None


@dataclass(frozen=True)
class HistoryPoint:
    captured_at: datetime
    subscribers: int | None
    views: int | None


@dataclass(frozen=True)
class OwnerDay:
    day: date
    views: int
    subscribers_gained: int | None
    subscribers_lost: int | None


@dataclass(frozen=True)
class ChannelInput:
    facts: ChannelFacts
    role: ChannelRole
    # public uploads from at least two windows back, so the previous window's pace can be read
    videos: Sequence[VideoRecord]
    # oldest upload we hold for the channel; the previous window counts only when it reaches that far
    oldest_upload_at: datetime | None
    history: Sequence[HistoryPoint] = ()
    owner_days: Sequence[OwnerDay] = ()


def _engagement(video: VideoRecord, rates: RateSettings) -> float | None:
    return compute_rates(video.view_count, video.like_count, video.comment_count, rates).engagement_rate


def format_stats(videos: Sequence[VideoRecord], window_days: int, rates: RateSettings) -> FormatStats:
    views = [video.view_count for video in videos]
    engagement = [rate for video in videos if (rate := _engagement(video, rates)) is not None]
    return FormatStats(
        uploads=len(videos),
        uploads_per_week=len(videos) / (window_days / 7),
        total_views=sum(views),
        mean_views=sum(views) / len(views) if views else None,
        median_views=median(views),
        mean_engagement_rate=sum(engagement) / len(engagement) if engagement else None,
        rated_uploads=len(engagement),
    )


def _pct(change: int | float, base: int | float | None) -> float | None:
    return change / base if base else None


def history_growth(
    metric: str, points: Sequence[HistoryPoint], as_of: datetime, days: int
) -> GrowthFigure | None:
    since = as_of - timedelta(days=days)
    values = sorted(
        (point.captured_at, value)
        for point in points
        if since <= point.captured_at <= as_of
        and (value := point.subscribers if metric == "subscribers" else point.views) is not None
    )
    if len(values) < 2 or values[0][0].date() == values[-1][0].date():
        return None
    (first_at, first), (last_at, last) = values[0], values[-1]
    return GrowthFigure(
        metric=metric,
        source="channel_history",
        requested_days=days,
        start_day=first_at.date(),
        end_day=last_at.date(),
        days_covered=(last_at.date() - first_at.date()).days,
        start_value=first,
        end_value=last,
        change=last - first,
        change_pct=_pct(last - first, first),
    )


def owner_growth(
    metric: str, owner_days: Sequence[OwnerDay], current: int | None, as_of: datetime, days: int
) -> GrowthFigure | None:
    since = as_of.date() - timedelta(days=days)
    rows = [row for row in owner_days if since <= row.day <= as_of.date()]
    if not rows:
        return None
    if metric == "subscribers":
        change = sum(row.subscribers_gained or 0 for row in rows) - sum(
            row.subscribers_lost or 0 for row in rows
        )
    else:
        change = sum(row.views for row in rows)
    start = current - change if current is not None else None
    first, last = min(row.day for row in rows), max(row.day for row in rows)
    return GrowthFigure(
        metric=metric,
        source="youtube_analytics",
        requested_days=days,
        start_day=first,
        end_day=last,
        days_covered=(last - first).days + 1,
        start_value=start,
        end_value=current,
        change=change,
        change_pct=_pct(change, start),
    )


def _growth(channel: ChannelInput, metric: str, as_of: datetime, days: int) -> GrowthFigure | None:
    current = channel.facts.subscribers if metric == "subscribers" else channel.facts.total_views
    # exact owner analytics wins over our own daily public totals
    return owner_growth(metric, channel.owner_days, current, as_of, days) or history_growth(
        metric, channel.history, as_of, days
    )


def channel_overview(
    channel: ChannelInput,
    as_of: datetime,
    rates: RateSettings,
    *,
    window_days: int = WINDOW_DAYS,
    growth_days: int = GROWTH_DAYS,
) -> ChannelOverview:
    facts = channel.facts
    start = as_of - timedelta(days=window_days)
    previous_start = start - timedelta(days=window_days)
    published = [
        video
        for video in channel.videos
        if video.availability == "available"
        and video.privacy_status == "public"
        and video.effective_published_at <= as_of
    ]
    in_window = [video for video in published if video.effective_published_at >= start]
    stat_videos = [video for video in in_window if video.format in STAT_FORMATS]
    overall = format_stats(stat_videos, window_days, rates)
    by_format = {
        fmt.value: format_stats(videos, window_days, rates)
        for fmt in STAT_FORMATS
        if (videos := [video for video in stat_videos if video.format == fmt])
    }
    reaches_previous = (facts.published_at is not None and facts.published_at >= previous_start) or (
        channel.oldest_upload_at is not None and channel.oldest_upload_at <= previous_start
    )
    previous = (
        sum(
            1
            for video in published
            if previous_start <= video.effective_published_at < start and video.format in STAT_FORMATS
        )
        if reaches_previous
        else None
    )
    gaps = gaps_in_days([video.effective_published_at for video in stat_videos])
    last_upload = max((video.effective_published_at for video in published), default=None)
    ranked = sorted(stat_videos, key=lambda video: (-video.view_count, video.video_id))[:TOP_UPLOADS]
    return ChannelOverview(
        channel_id=facts.channel_id,
        role=channel.role,
        title=facts.title,
        as_of=as_of,
        window_days=window_days,
        subscribers=facts.subscribers,
        total_views=facts.total_views,
        total_videos=facts.total_videos,
        format_mix={fmt.value: sum(1 for v in in_window if v.format == fmt) for fmt in VideoFormat},
        overall=overall,
        by_format=by_format,
        views_share={
            fmt: stats.total_views / overall.total_views
            for fmt, stats in by_format.items()
            if overall.total_views
        },
        previous_window_uploads=previous,
        pace_change_pct=_pct(overall.uploads - previous, previous) if previous is not None else None,
        days_since_last_upload=(as_of - last_upload).total_seconds() / 86_400 if last_upload else None,
        median_gap_days=median(gaps),
        longest_gap_days=max(gaps, default=None),
        views_per_subscriber=_pct(overall.mean_views, facts.subscribers) if overall.mean_views else None,
        top_uploads=[
            TopUpload(
                video_id=video.video_id,
                title=video.title,
                format=video.format.value,
                published_at=video.effective_published_at,
                views=video.view_count,
                engagement_rate=_engagement(video, rates),
            )
            for video in ranked
        ],
        subscriber_growth=_growth(channel, "subscribers", as_of, growth_days),
        view_growth=_growth(channel, "views", as_of, growth_days),
        scraped_uploads=sum(1 for video in in_window if video.data_source == "yt_dlp"),
    )


MEDIAN_METRICS = {
    "uploads_per_week": lambda o: o.overall.uploads_per_week,
    "short_form_uploads": lambda o: o.format_mix.get(VideoFormat.short_form.value, 0),
    "long_form_uploads": lambda o: o.format_mix.get(VideoFormat.long_form.value, 0),
    "mean_views": lambda o: o.overall.mean_views,
    "median_views": lambda o: o.overall.median_views,
    "mean_engagement_rate": lambda o: o.overall.mean_engagement_rate,
    "views_per_subscriber": lambda o: o.views_per_subscriber,
    "days_since_last_upload": lambda o: o.days_since_last_upload,
    "subscriber_growth_pct": lambda o: o.subscriber_growth.change_pct if o.subscriber_growth else None,
    "view_growth_pct": lambda o: o.view_growth.change_pct if o.view_growth else None,
}


def channel_landscape(
    own: ChannelInput | None,
    competitors: Sequence[ChannelInput],
    as_of: datetime,
    rates: RateSettings,
    *,
    window_days: int = WINDOW_DAYS,
    growth_days: int = GROWTH_DAYS,
) -> ChannelLandscape:
    def overview(channel: ChannelInput) -> ChannelOverview:
        return channel_overview(channel, as_of, rates, window_days=window_days, growth_days=growth_days)

    sheets = sorted(
        (overview(channel) for channel in competitors), key=lambda o: (o.title.casefold(), o.channel_id)
    )
    return ChannelLandscape(
        as_of=as_of,
        window_days=window_days,
        growth_days=growth_days,
        own=overview(own) if own else None,
        competitors=sheets,
        competitor_median={
            name: median([value for sheet in sheets if (value := read(sheet)) is not None])
            for name, read in MEDIAN_METRICS.items()
        },
    )
