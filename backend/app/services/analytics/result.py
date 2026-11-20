import hashlib
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.domain.analytics import (
    ChannelBaseline,
    ChannelScorecard,
    CommentSignals,
    ConversionLens,
    GapMatrix,
    OutlierRecord,
    OwnPerformanceReport,
    TraitLift,
    TrendingReport,
    VideoMetrics,
    VideoTraits,
)
from app.domain.recommendations import DATA_DISCLOSURE

SameAgeMode = Literal["true", "proxy", "mixed_by_video"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WindowAnalytics(_Frozen):
    window_days: int
    comparison_age_days: int
    secondary_age_days: int | None = None
    same_age_mode: dict[str, SameAgeMode]
    lifts: dict[str, list[TraitLift]]
    outliers: dict[str, list[OutlierRecord]]
    unusual_spikes: dict[str, list[OutlierRecord]]
    scorecards: list[ChannelScorecard]
    own_report: OwnPerformanceReport | None
    trending: TrendingReport
    gap_matrix: GapMatrix
    conversion: ConversionLens
    sufficiency_messages: list[str] = []
    disclosure: str = DATA_DISCLOSURE


class AnalyticsResult(_Frozen):
    """the analytics stage output handed to 07, 08 and 09; every number here is calculated by GrowthHacker."""

    client_id: str
    as_of: AwareDatetime
    metrics: dict[int, dict[str, VideoMetrics]]
    baselines: list[ChannelBaseline]
    traits: dict[str, VideoTraits]
    comment_signals: dict[str, CommentSignals]
    windows: dict[int, WindowAnalytics]
    excluded: dict[str, str] = {}
    settings_hash: str
    versions: dict[str, str]
    disclosure: str = DATA_DISCLOSURE

    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()
