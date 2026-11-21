from app.domain.topics import TopicKind
from app.domain.trends import KeywordCandidate, KeywordSource, KeywordTrend, TrendVideo
from tests.news.fixtures import NOW
from tests.refresh.fixtures import CLIENT_ID, topic_id, topic_ref

KNEE = topic_ref("knee replacement", TopicKind.offering)
HEART = topic_ref("heart attack", TopicKind.keyword)
CHANNELS = [f"UCtrendchannel{i:010d}" for i in range(8)]


def candidate(keyword: str, ref=KNEE, sources=(KeywordSource.autocomplete,)) -> KeywordCandidate:
    return KeywordCandidate(
        keyword=keyword, sources=list(sources), topic_ref_id=ref.id, topic_label=ref.label
    )


def trend_video(index: int, title: str, views_per_day: float, channel: int | None = None) -> TrendVideo:
    return TrendVideo(
        video_id=f"trendvid{index:03d}",
        channel_id=CHANNELS[(index if channel is None else channel) % len(CHANNELS)],
        channel_title="channel",
        title=title,
        published_at=NOW,
        views=int(views_per_day * 2),
        age_days=2.0,
        views_per_day=views_per_day,
    )


def keyword_trend(
    keyword: str, median: float | None, videos: int = 6, channels: int = 4, **extra
) -> KeywordTrend:
    return KeywordTrend(
        candidate=extra.pop("candidate", candidate(keyword)),
        status="checked",
        checked_at=NOW,
        window_days=14,
        recent_videos=videos,
        channels=channels,
        median_views_per_day=median,
        leaders_views_per_day=median,
        top_views_per_day=median * 3 if median else None,
        **extra,
    )


__all__ = ["CLIENT_ID", "NOW", "topic_id"]
