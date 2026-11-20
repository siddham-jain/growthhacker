"""leakage canary (plan 12 §5.1.7): plant rows dated after T, replay, and demand byte-identical output."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.domain.youtube import VideoRecord, VideoSnapshot
from app.services.analytics.inputs import AnalyticsInputs

ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
PLANTED_VIDEOS_PER_CHANNEL = 2
VIEW_INFLATION = 1000


@dataclass(frozen=True)
class CanaryResult:
    passed: bool
    original: str
    perturbed: str
    planted_rows: int


def _video_id(rng: np.random.Generator) -> str:
    return "".join(ID_ALPHABET[i] for i in rng.integers(0, len(ID_ALPHABET), size=11))


def _snapshot(video: VideoRecord, at: datetime, views: int) -> VideoSnapshot:
    return VideoSnapshot(
        video_id=video.video_id,
        slot_at=at.replace(minute=0, second=0, microsecond=0),
        captured_at=at,
        age_hours=max(0.0, (at - video.published_at).total_seconds() / 3600),
        view_count=views,
        like_count=views // 10,
        comment_count=views // 100,
        source="batch_stats",
        counting_regime="standard",
    )


def perturb(
    inputs: AnalyticsInputs, as_of: datetime, rng: np.random.Generator
) -> tuple[AnalyticsInputs, int]:
    """adds viral post-T uploads, post-T snapshots, comments, thumbnails and topic matches."""
    snapshots = {vid: list(items) for vid, items in inputs.snapshots.items()}
    comments = {vid: list(items) for vid, items in inputs.comments.items()}
    thumbnails = dict(inputs.thumbnails)
    matches = list(inputs.topic_matches)
    videos = list(inputs.videos)
    planted = 0
    for video in inputs.videos:
        later = as_of + timedelta(hours=1 + float(rng.integers(0, 48)))
        snapshots.setdefault(video.video_id, []).append(
            _snapshot(video, later, video.view_count * VIEW_INFLATION)
        )
        planted += 1
        for item in inputs.comments.get(video.video_id, [])[:1]:
            comments[video.video_id].append(
                item.model_copy(update={"comment_id": item.comment_id + "x", "fetched_at": later})
            )
            planted += 1
    by_video = {m.video_id: m for m in inputs.topic_matches}
    templates = {v.channel_id: v for v in inputs.videos}
    for template in templates.values():
        for offset in range(PLANTED_VIDEOS_PER_CHANNEL):
            published = as_of + timedelta(days=1 + offset)
            clone = template.model_copy(
                update={
                    "video_id": _video_id(rng),
                    "published_at": published,
                    "effective_published_at": published,
                    "metadata_fetched_at": published + timedelta(days=8),
                    "view_count": template.view_count * VIEW_INFLATION,
                }
            )
            videos.append(clone)
            snapshots[clone.video_id] = [
                _snapshot(clone, published + timedelta(days=day), clone.view_count * day) for day in (1, 3, 7)
            ]
            if template.video_id in inputs.thumbnails:
                thumbnails[clone.video_id] = inputs.thumbnails[template.video_id].model_copy(
                    update={"video_id": clone.video_id, "computed_at": published + timedelta(hours=1)}
                )
            if template.video_id in by_video:
                matches.append(
                    by_video[template.video_id].model_copy(
                        update={"video_id": clone.video_id, "matched_at": published + timedelta(hours=2)}
                    )
                )
            planted += 4
    for match in inputs.topic_matches[:5]:
        matches.append(
            match.model_copy(update={"matched_at": as_of + timedelta(days=2), "combined_score": 1.0})
        )
        planted += 1
    perturbed = inputs.model_copy(
        update={
            "videos": videos,
            "snapshots": snapshots,
            "comments": comments,
            "thumbnails": thumbnails,
            "topic_matches": matches,
        }
    )
    return perturbed, planted


def run_canary(
    inputs: AnalyticsInputs, as_of: datetime, replay: Callable[[AnalyticsInputs], str], seed: int
) -> CanaryResult:
    original = replay(inputs)
    perturbed, planted = perturb(inputs, as_of, np.random.default_rng(seed))
    replayed = replay(perturbed)
    return CanaryResult(
        passed=original == replayed, original=original, perturbed=replayed, planted_rows=planted
    )
