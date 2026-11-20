from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.common import ChannelRole
from app.domain.confidence import Confidence, ConfidenceLabel
from app.domain.recommendations import (
    ExpectedRange,
    Lens,
    Reason,
    Recommendation,
    RecommendationExtras,
    RecType,
    ReferenceVideo,
    VideoIdeaSubject,
    make_stable_key,
    recommendation_id,
)

CLIENT_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-1a2b3c4d5e6f")
RUN_ID = UUID("0190f2a4-6c1e-7d3a-9b2f-aaaaaaaaaaaa")
CHANNEL_IDS = (
    "UC0InVdvqNyNzKBl1-TL348A",
    "UCfortisHealthcare0000AA",
    "UCnarayanaHealth00000000",
    "UCmedantaTheMedicity0000",
)
VIDEO_IDS = ("aaaaaaaaaa1", "bbbbbbbbbb2", "cccccccccc3", "dddddddddd4", "eeeeeeeeee5")
GENERATED_AT = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
TOPIC_ID = "tr_0123456789ab"


def make_views_at_age(age_days: int = 7, views: int | None = 18_400, method: AgeMethod = AgeMethod.observed):
    return ViewsAtAgeSummary(
        age_days=age_days,
        views=views,
        method=method,
        label=f"views at day {age_days} ({method.value})",
    )


def make_reference_video(index: int = 0, **overrides: Any) -> ReferenceVideo:
    video_id = VIDEO_IDS[index % len(VIDEO_IDS)]
    fields: dict[str, Any] = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": f"Knee replacement cost explained, part {index + 1}",
        "channel": f"Hospital channel {index + 1}",
        "channel_id": CHANNEL_IDS[(index % (len(CHANNEL_IDS) - 1)) + 1],
        "channel_role": ChannelRole.competitor,
        "published_at": GENERATED_AT - timedelta(days=17 + index),
        "views_day7": 18_400,
        "views_at_age": make_views_at_age(),
        "outlier_ratio": 3.6,
        "channel_baseline_views_at_age": 5_100.0,
        "is_outlier": True,
        "format_key": "long_form|4-8m",
        "length_band": "4-8m",
        "matched_topic_ids": [TOPIC_ID],
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "has_paid_product_placement": False,
    }
    fields.update(overrides)
    return ReferenceVideo(**fields)


def make_reason(reason_id: str = "r1") -> Reason:
    return Reason(
        reason_id=reason_id,
        code="OUTLIER_SHARE",
        params={"k": 4, "n": 6, "window": 30, "topic": "knee replacement cost"},
        statement="4 of 6 outliers in the last 30 days cover knee replacement cost",
        ref_video_ids=list(VIDEO_IDS[:3]),
        source="youtube_public",
    )


def make_extras(stable_key: str, **overrides: Any) -> RecommendationExtras:
    fields: dict[str, Any] = {
        "stable_key": stable_key,
        "run_id": RUN_ID,
        "rendered_by": "llm",
        "first_seen_at": GENERATED_AT,
        "expires_at": GENERATED_AT + timedelta(days=10),
        "why_it_works_structured": [make_reason()],
        "expected_range": ExpectedRange(
            low=1.2,
            high=2.0,
            basis="blended",
            n_peer=23,
            n_peer_effective=17.4,
            n_own=7,
            comparison_age_days=7,
            display="1.2x-2.0x (indicative)",
        ),
        "subject": VideoIdeaSubject(topic_id=TOPIC_ID, topic_text="knee replacement cost"),
    }
    fields.update(overrides)
    return RecommendationExtras(**fields)


def make_recommendation(reference_count: int = 3, **overrides: Any) -> Recommendation:
    rec_type = overrides.pop("type", RecType.video_idea)
    stable_key = make_stable_key(CLIENT_ID, rec_type, "topic:knee-replacement-cost|long_form", 30)
    fields: dict[str, Any] = {
        "id": recommendation_id(stable_key),
        "type": rec_type,
        "summary": "Short explainer on knee replacement costs in Chennai, under 8 minutes",
        "lens": [Lens.views, Lens.conversion],
        "buyer_question": "How much does knee replacement cost in Chennai?",
        "why_it_works": [
            "4 of 6 niche outliers in 30 days are cost explainers",
            "2 competitors cover this; you don't",
        ],
        "reference_videos": [make_reference_video(i) for i in range(reference_count)],
        "fit_to_client": "Matches 2 tracked buyer questions; your how-tos average 1.4x your baseline",
        "expected_range_vs_baseline": "1.2x-2.0x (indicative)",
        "confidence": Confidence(label=ConfidenceLabel.high, outliers=6, channels=3, matching_videos=23),
        "window_days": 30,
        "generated_at": GENERATED_AT,
        "extras": make_extras(stable_key),
    }
    fields.update(overrides)
    return Recommendation(**fields)
