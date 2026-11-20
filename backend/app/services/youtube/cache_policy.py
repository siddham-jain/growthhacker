from datetime import datetime, timedelta
from enum import StrEnum

from app.core.config import YouTubeFreshnessSettings
from app.core.registry import Registry


class DataKind(StrEnum):
    alias = "alias"
    alias_not_found = "alias_not_found"
    channel_metadata = "channel_metadata"
    channel_stats = "channel_stats"
    uploads_own_competitor = "uploads_own_competitor"
    uploads_niche = "uploads_niche"
    video_metadata = "video_metadata"
    own_video_metadata = "own_video_metadata"
    comment_sample = "comment_sample"
    search = "search"


HARD_CAP = timedelta(days=28)

freshness_registry: Registry[str] = Registry("youtube_freshness")
for _kind, _setting in (
    (DataKind.alias, "alias_h"),
    (DataKind.alias_not_found, "alias_not_found_h"),
    (DataKind.channel_metadata, "channel_metadata_h"),
    (DataKind.channel_stats, "channel_stats_h"),
    (DataKind.uploads_own_competitor, "uploads_own_competitor_h"),
    (DataKind.uploads_niche, "uploads_niche_h"),
    (DataKind.video_metadata, "video_metadata_h"),
    (DataKind.own_video_metadata, "own_video_metadata_h"),
    (DataKind.comment_sample, "comment_sample_h"),
):
    freshness_registry.add(_kind.value, _setting)


class FreshnessPolicy:
    """re-fetch when older than the ttl; nothing may outlive the 28-day policy act-by age."""

    def __init__(self, settings: YouTubeFreshnessSettings) -> None:
        self._settings = settings

    def ttl(self, kind: DataKind, max_age_s: int | None = None) -> timedelta:
        if max_age_s is not None:
            return min(timedelta(seconds=max_age_s), HARD_CAP)
        hours = getattr(self._settings, freshness_registry.get(kind.value))
        return min(timedelta(hours=hours), HARD_CAP)

    def is_fresh(
        self, fetched_at: datetime | None, kind: DataKind, now: datetime, max_age_s: int | None = None
    ) -> bool:
        return fetched_at is not None and now - fetched_at < self.ttl(kind, max_age_s)
