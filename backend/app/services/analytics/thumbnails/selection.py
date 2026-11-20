import hashlib
from datetime import datetime, timedelta

from app.domain.analytics import OutlierTier
from app.domain.youtube import VideoRecord
from app.services.analytics.baselines import AgeFamily
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.state import AssessmentBook, StateBuilder

# breakouts are recommendation reference candidates, so they count as likely outliers
LIKELY_OUTLIER_TIERS = frozenset({OutlierTier.breakout, OutlierTier.outlier, OutlierTier.strong_outlier})


def in_control_sample(video_id: str, share: float) -> bool:
    # a stable hash keeps the same videos in the sample run after run, independent of performance
    bucket = int.from_bytes(hashlib.sha256(video_id.encode()).digest()[:8], "big") / 2**64
    return bucket < share


def likely_outliers(inputs: AnalyticsInputs, settings: AnalyticsEngineSettings, as_of: datetime) -> set[str]:
    # the same states and baselines the analytics stage builds next, at every comparison age and family
    states, _ = StateBuilder(settings, as_of).build(inputs.visible_at(as_of))
    book = AssessmentBook(states.values(), settings, as_of)
    return {
        state.video_id
        for state in states.values()
        for age in settings.comparison_ages()
        for assessed in (book.true_assessment(state, age), book.assess(state, age, AgeFamily.proxy))
        if assessed is not None and assessed.tier in LIKELY_OUTLIER_TIERS
    }


def window_videos(
    inputs: AnalyticsInputs, settings: AnalyticsEngineSettings, as_of: datetime
) -> list[VideoRecord]:
    start = as_of - timedelta(days=max(settings.windows))
    return [v for v in inputs.videos if v.published_at >= start and v.thumbnails]


def ocr_scope(
    inputs: AnalyticsInputs, settings: AnalyticsEngineSettings, as_of: datetime, control_share: float
) -> frozenset[str]:
    """window videos whose thumbnail text is read: own uploads, likely outliers and a stable control sample."""
    likely = likely_outliers(inputs, settings, as_of)
    return frozenset(
        v.video_id
        for v in window_videos(inputs, settings, as_of)
        if v.channel_id == inputs.own_channel_id
        or v.video_id in likely
        or in_control_sample(v.video_id, control_share)
    )
