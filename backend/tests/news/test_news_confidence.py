from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.confidence import ConfidenceBasis
from app.domain.news import DemandCheck, EarlyVideo
from app.services.news.opportunity import news_confidence

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def early(index: int, ratio: float | None) -> EarlyVideo:
    return EarlyVideo(
        video_id=f"vid{index:08d}",
        channel_id=f"UC{index:022d}",
        channel_title=f"Channel {index}",
        published_at=NOW - timedelta(hours=6),
        age_hours=6,
        views=5,
        views_per_hour=1,
        views_at_age=ViewsAtAgeSummary(age_days=0, views=5, method=AgeMethod.observed, label="views at 6h"),
        outlier_ratio=ratio,
        is_outlier=bool(ratio and ratio >= 3),
        duration_band="short",
    )


def signal(videos: list[EarlyVideo]) -> SimpleNamespace:
    demand = DemandCheck(
        status="checked",
        entity_query="gpt",
        published_after=NOW - timedelta(days=2),
        checked_at=NOW,
        early_videos=videos,
        early_video_count=len(videos),
        channel_count=len(videos),
        outlier_count=sum(video.is_outlier for video in videos),
        median_views_per_hour=1,
        saturation="emerging",
        quota_units_spent=1,
    )
    return SimpleNamespace(
        demand=demand, judgement=SimpleNamespace(confirmation=SimpleNamespace(value="confirmed"))
    )


def test_videos_without_a_channel_baseline_do_not_count_towards_a_full_card():
    unbaselined = [early(index, None) for index in range(5)]
    assert news_confidence(signal(unbaselined), 3).basis is ConfidenceBasis.first_mover
    baselined = [early(index, 4.0) for index in range(3)]
    assert news_confidence(signal(baselined), 3).basis is ConfidenceBasis.standard
