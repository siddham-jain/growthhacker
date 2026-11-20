from datetime import datetime

from app.domain.youtube import VideoRecord

SECONDS_PER_DAY = 86_400.0


class AgeClock:
    """age conventions on top of the run's as_of; nothing here reads the wall clock."""

    def __init__(self, as_of: datetime) -> None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        self.as_of = as_of

    @staticmethod
    def anchor(video: VideoRecord) -> datetime:
        # a scheduled stream's published_at can be the scheduling time, so the actual start wins
        return video.live_actual_start_at or video.published_at

    @staticmethod
    def age_between(anchor: datetime, at: datetime) -> float:
        return (at - anchor).total_seconds() / SECONDS_PER_DAY

    def age_days(self, video: VideoRecord) -> float:
        return self.age_between(self.anchor(video), self.as_of)
