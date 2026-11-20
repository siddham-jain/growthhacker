from datetime import timedelta

from app.core.config import RecommendFeedbackSettings
from app.domain.recommendations import FeedbackStatus
from app.services.recommend.evidence.index import OwnVideo
from app.services.recommend.feedback import (
    FeedbackRow,
    OutcomeTracker,
    TrackedCard,
    apply_feedback,
    feedback_state,
)
from app.services.recommend.fingerprint import feedback_group
from app.services.recommend.inputs import ClientRecommendConfig
from tests.recommend.factories import TOPIC, draft, reference
from tests.recommend.fixtures import AS_OF

SETTINGS = RecommendFeedbackSettings()


def row(key: str, status: FeedbackStatus | None, days_ago: float) -> FeedbackRow:
    return FeedbackRow(stable_key=key, status=status, updated_at=AS_OF - timedelta(days=days_ago))


def test_latest_feedback_per_key_decides_what_stays_hidden() -> None:
    state = feedback_state(
        [
            row("dismissed-recently", FeedbackStatus.dismissed, 10),
            row("dismissed-long-ago", FeedbackStatus.dismissed, 120),
            row("snoozed", FeedbackStatus.snoozed, 3),
            row("snooze-over", FeedbackStatus.snoozed, 20),
            row("made-it", FeedbackStatus.done, 200),
            row("saved", FeedbackStatus.saved, 1),
            row("undone", FeedbackStatus.dismissed, 5),
            row("undone", None, 1),
        ],
        AS_OF,
        SETTINGS,
    )

    assert state.hidden_keys == {"dismissed-recently": "dismissed", "snoozed": "snoozed", "made-it": "done"}


def test_dismissal_penalises_siblings_and_reasons_have_their_own_effects() -> None:
    idea = draft([reference(index) for index in range(3)])
    config = ClientRecommendConfig()

    dismissed = apply_feedback(
        config, idea, FeedbackStatus.dismissed, "cannot_produce_format", AS_OF, SETTINGS
    )
    (penalty,) = dismissed.config.feedback_penalties
    assert penalty.group_key == feedback_group(idea) == f"topic:{TOPIC}"
    assert penalty.factor == 0.5 and penalty.until == AS_OF + timedelta(days=60)
    assert dismissed.config.excluded_formats == ["long_form|3–8m"]

    off_topic = apply_feedback(config, idea, FeedbackStatus.dismissed, "not_our_business", AS_OF, SETTINGS)
    assert [(item.kind, item.value) for item in off_topic.proposals] == [("stop_tracking_topic", TOPIC)]

    wrong = apply_feedback(config, idea, FeedbackStatus.dismissed, "evidence_wrong", AS_OF, SETTINGS)
    assert [item.kind for item in wrong.proposals] == ["evidence_review"]

    saved = apply_feedback(config, idea, FeedbackStatus.saved, None, AS_OF, SETTINGS)
    assert saved.config == config


def test_expired_effects_are_dropped_when_config_is_rewritten() -> None:
    idea = draft([reference(index) for index in range(3)])
    first = apply_feedback(ClientRecommendConfig(), idea, FeedbackStatus.dismissed, None, AS_OF, SETTINGS)
    later = apply_feedback(
        first.config, idea, FeedbackStatus.saved, None, AS_OF + timedelta(days=90), SETTINGS
    )

    assert later.config.feedback_penalties == []


def own(video_id: str, days_ago: float, ratio: float | None, topics=frozenset({TOPIC})) -> OwnVideo:
    return OwnVideo(
        video_id=video_id,
        title="Own",
        published_at=AS_OF - timedelta(days=days_ago),
        age_days=days_ago,
        format_key="long_form|3–8m",
        topic_ids=topics,
        ratio=ratio,
        is_outlier=bool(ratio and ratio >= 3),
        traits={},
    )


def card(**fields) -> TrackedCard:
    values = {
        "stable_key": "card",
        "topic_ids": (TOPIC,),
        "format_key": "long_form|3–8m",
        "first_seen_at": AS_OF - timedelta(days=20),
        "predicted_low": 1.2,
        "predicted_high": 2.0,
        "comparison_age_days": 7,
    }
    values.update(fields)
    return TrackedCard(**values)


def test_outcomes_link_user_marked_and_detected_videos_at_the_comparison_age() -> None:
    tracker = OutcomeTracker(auto_done_days=30)
    videos = [own("ownvideoaaa", 10, 1.5), own("ownvideobbb", 12, 3.4), own("ownvideoccc", 2, 1.0)]

    (linked,) = tracker.measure([card(linked_video_id="ownvideobbb")], videos, set())
    assert (linked.matched_by, linked.within_range, linked.became_outlier) == ("user", False, True)

    (detected,) = tracker.measure([card()], videos, set())
    assert (detected.video_id, detected.matched_by, detected.within_range) == ("ownvideobbb", "auto", False)

    assert tracker.measure([card(linked_video_id="ownvideoccc")], videos, set()) == []
    assert tracker.measure([card()], videos, {"card"}) == []
