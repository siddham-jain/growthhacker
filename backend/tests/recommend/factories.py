from datetime import timedelta
from typing import Any

from app.domain.analytics import AgeMethod, OutlierTier, ViewsAtAgeSummary
from app.domain.common import ChannelRole, FormatSegment
from app.domain.recommendations import (
    PatternSpec,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    VideoIdeaSubject,
    make_stable_key,
)
from app.services.recommend.evidence.index import ClientBaseline, EvidenceVideo
from tests.recommend.fixtures import AS_OF, CLIENT_ID, channel_id, topic_id, video_id

TOPIC = topic_id("bq_knee")


def evidence(
    index: int, ratio: float = 3.5, *, channel: str | None = None, baseline: float = 2_000, **fields: Any
) -> EvidenceVideo:
    values: dict[str, Any] = {
        "video_id": video_id(f"e{index}"),
        "channel_id": channel_id(channel or f"c{index}"),
        "channel_title": f"Channel {channel or index}",
        "role": ChannelRole.competitor,
        "title": f"Video {index}",
        "description": "",
        "published_at": AS_OF - timedelta(days=5 + index),
        "age_days": 5.0 + index,
        "thumbnail_url": None,
        "has_paid_product_placement": False,
        "views_at_age": ViewsAtAgeSummary(
            age_days=7, views=round(ratio * baseline), method=AgeMethod.observed, label="day 7"
        ),
        "ratio": ratio,
        "is_outlier": ratio >= 3.0,
        "tier": OutlierTier.outlier if ratio >= 3 else OutlierTier.normal,
        "baseline_median": baseline,
        "baseline_n": 20,
        "segment": FormatSegment.long_form,
        "format_key": "long_form|3–8m",
        "length_band": "3–8m",
        "traits": {},
        "topic_ids": frozenset({TOPIC}),
    }
    values.update(fields)
    return EvidenceVideo(**values)


def reference(
    index: int,
    *,
    channel: str | None = None,
    outlier: bool = True,
    method: AgeMethod = AgeMethod.observed,
    days_ago: float = 5,
    **fields: Any,
) -> ReferenceVideo:
    values: dict[str, Any] = {
        "video_id": video_id(f"r{index}"),
        "url": f"https://www.youtube.com/watch?v={video_id(f'r{index}')}",
        "title": f"Reference {index}",
        "channel": f"Channel {channel or index}",
        "channel_id": channel_id(channel or f"c{index}"),
        "channel_role": ChannelRole.competitor,
        "published_at": AS_OF - timedelta(days=days_ago),
        "views_day7": None,
        "views_at_age": ViewsAtAgeSummary(age_days=7, views=9_000, method=method, label="day 7"),
        "outlier_ratio": 3.4 if outlier else 1.1,
        "channel_baseline_views_at_age": 2_500.0,
        "is_outlier": outlier,
        "format_key": "long_form|3–8m",
        "length_band": "3–8m",
        "matched_topic_ids": [TOPIC],
    }
    values.update(fields)
    return ReferenceVideo(**values)


def draft(
    refs: list[ReferenceVideo], rec_type: RecType = RecType.video_idea, *, key: str = "k", **fields: Any
) -> RecommendationDraft:
    if rec_type is RecType.video_idea:
        subject = VideoIdeaSubject(
            topic_id=TOPIC, topic_text="knee replacement cost", format_key="long_form|3–8m"
        )
    else:
        subject = PatternSpec(
            kind="title",
            traits={"title.is_question": "true"},
            scope="competitor_set",
            lift=2.0,
            ci_low=1.4,
            ci_high=3.0,
            n_with=10,
            n_outliers_with=5,
            n_total=40,
            n_outliers_total=9,
        )
    stable = make_stable_key(CLIENT_ID, rec_type, key, 30)
    values: dict[str, Any] = {
        "draft_id": f"d_{stable[:16]}",
        "stable_key": stable,
        "client_id": CLIENT_ID,
        "type": rec_type,
        "window_days": 30,
        "as_of": AS_OF,
        "produced_by": "test@1",
        "subject": subject,
        "topic_ids": [TOPIC],
        "reference_pool": refs,
        "references": refs[:5],
    }
    values.update(fields)
    return RecommendationDraft(**values)


def baseline(views: float = 2_000, n: int = 30) -> ClientBaseline:
    return ClientBaseline(views_at_age=views, n=n)
