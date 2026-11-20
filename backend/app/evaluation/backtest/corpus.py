from datetime import datetime, timedelta

from app.domain.analytics import VideoTraits
from app.domain.youtube import VideoRecord
from app.evaluation.backtest.models import CorpusVideo
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.result import AnalyticsResult
from app.services.recommend.evidence.traits import flatten_traits


def point_in_time(inputs: AnalyticsInputs, as_of: datetime) -> AnalyticsInputs:
    """06's visible_at, plus dropping topic matches recorded after T (visible_at keeps them) and every row
    that belongs to a video not yet published at T."""
    visible = inputs.visible_at(as_of)
    ids = {v.video_id for v in visible.videos}
    return visible.model_copy(
        update={
            "snapshots": {vid: items for vid, items in visible.snapshots.items() if vid in ids},
            "comments": {vid: items for vid, items in visible.comments.items() if vid in ids},
            "thumbnails": {vid: item for vid, item in visible.thumbnails.items() if vid in ids},
            "topic_matches": [
                m for m in visible.topic_matches if m.video_id in ids and m.matched_at <= as_of
            ],
            "directness": [d for d in visible.directness if d.video_id in ids],
        }
    )


def views_at(inputs: AnalyticsInputs, video: VideoRecord, as_of: datetime) -> int | None:
    # raw lifetime views as they stood at T: the latest snapshot at or before T, never a later fetch
    captured = [s for s in inputs.snapshots.get(video.video_id, []) if s.captured_at <= as_of]
    if captured:
        return max(captured, key=lambda s: s.captured_at).view_count
    return video.view_count if video.metadata_fetched_at <= as_of else None


def corpus_video(
    traits: VideoTraits,
    channel_id: str,
    published_at: datetime,
    *,
    views: int | None = None,
    own: bool = False,
) -> CorpusVideo:
    return CorpusVideo(
        video_id=traits.video_id,
        channel_id=channel_id,
        published_at=published_at,
        segment=traits.segment.value,
        traits=flatten_traits(traits),
        topic_ids=frozenset(topic.topic_ref_id for topic in traits.topics),
        topic_scores={topic.topic_ref_id: topic.score for topic in traits.topics},
        views_at_t=views,
        is_own=own,
    )


def window_corpus(
    inputs: AnalyticsInputs, result: AnalyticsResult, as_of: datetime, window_days: int
) -> list[CorpusVideo]:
    """pre-T uploads in [T - W, T) with traits, the pool copy_top and random draw from."""
    start = as_of - timedelta(days=window_days)
    own = inputs.own_channel_id
    videos = {v.video_id: v for v in inputs.videos if start <= v.published_at < as_of}
    return [
        corpus_video(
            result.traits[vid],
            video.channel_id,
            video.published_at,
            views=views_at(inputs, video, as_of),
            own=video.channel_id == own,
        )
        for vid, video in sorted(videos.items())
        if vid in result.traits
    ]
