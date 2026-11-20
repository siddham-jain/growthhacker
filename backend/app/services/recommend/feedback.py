from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.core.config import RecommendFeedbackSettings
from app.domain.recommendations import (
    CreatorSubject,
    FeedbackStatus,
    PatternSpec,
    RecommendationDraft,
    VideoIdeaSubject,
)
from app.services.recommend.evidence.index import OwnVideo
from app.services.recommend.fingerprint import feedback_group
from app.services.recommend.inputs import (
    ClientRecommendConfig,
    CreatorExclusion,
    FeedbackPenalty,
    FeedbackState,
)

FeedbackReason = Literal[
    "not_our_business",
    "already_covered",
    "cannot_produce_format",
    "creator_not_suitable",
    "evidence_wrong",
    "other",
]


@dataclass(frozen=True, slots=True)
class FeedbackRow:
    stable_key: str
    status: FeedbackStatus | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Proposal:
    # changes 07 only proposes; 05/03 own topic tracking and 12 owns evidence review
    kind: Literal["stop_tracking_topic", "evidence_review"]
    value: str


@dataclass(frozen=True, slots=True)
class FeedbackEffects:
    config: ClientRecommendConfig
    proposals: tuple[Proposal, ...]


def feedback_state(
    rows: Iterable[FeedbackRow], as_of: datetime, settings: RecommendFeedbackSettings
) -> FeedbackState:
    """the latest status per stable key decides whether a card stays hidden across daily runs."""
    latest: dict[str, FeedbackRow] = {}
    for row in sorted(rows, key=lambda item: item.updated_at):
        latest[row.stable_key] = row
    hidden: dict[str, str] = {}
    for key, row in sorted(latest.items()):
        age = as_of - row.updated_at
        if (
            row.status is FeedbackStatus.done
            or row.status is FeedbackStatus.dismissed
            and age <= timedelta(days=settings.dismiss_hide_days)
            or row.status is FeedbackStatus.snoozed
            and age <= timedelta(days=settings.snooze_days)
        ):
            hidden[key] = row.status.value
    return FeedbackState(hidden_keys=hidden)


def apply_feedback(
    config: ClientRecommendConfig,
    draft: RecommendationDraft,
    status: FeedbackStatus | None,
    reason: FeedbackReason | None,
    now: datetime,
    settings: RecommendFeedbackSettings,
) -> FeedbackEffects:
    """deterministic effects of one piece of feedback on the client's recommend config."""
    proposals: list[Proposal] = []
    excluded_formats = list(config.excluded_formats)
    excluded_creators = [item for item in config.excluded_creators if item.until > now]
    penalties = [item for item in config.feedback_penalties if item.until > now]
    subject = draft.subject
    if status is FeedbackStatus.dismissed:
        until = now + timedelta(days=settings.dismiss_penalty_days)
        penalties.append(
            FeedbackPenalty(group_key=feedback_group(draft), factor=settings.dismiss_penalty, until=until)
        )
    if reason == "cannot_produce_format":
        format_key = _format_of(draft)
        if format_key and format_key not in excluded_formats:
            excluded_formats.append(format_key)
    if reason == "creator_not_suitable" and isinstance(subject, CreatorSubject):
        until = now + timedelta(days=settings.creator_exclusion_days)
        excluded_creators.append(CreatorExclusion(channel_id=subject.channel_id, until=until))
    if reason == "not_our_business":
        proposals.extend(Proposal(kind="stop_tracking_topic", value=topic_id) for topic_id in draft.topic_ids)
    if reason == "evidence_wrong":
        proposals.append(Proposal(kind="evidence_review", value=draft.stable_key))
    updated = config.model_copy(
        update={
            "excluded_formats": sorted(excluded_formats),
            "excluded_creators": excluded_creators,
            "feedback_penalties": penalties,
        }
    )
    return FeedbackEffects(config=updated, proposals=tuple(proposals))


def _format_of(draft: RecommendationDraft) -> str | None:
    subject = draft.subject
    if isinstance(subject, VideoIdeaSubject):
        return subject.format_key
    if isinstance(subject, PatternSpec):
        return subject.traits.get("format_key")
    return None


@dataclass(frozen=True, slots=True)
class TrackedCard:
    stable_key: str
    topic_ids: tuple[str, ...]
    format_key: str | None
    first_seen_at: datetime
    predicted_low: float | None
    predicted_high: float | None
    comparison_age_days: int
    linked_video_id: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    stable_key: str
    video_id: str
    matched_by: Literal["user", "auto"]
    comparison_age_days: int
    predicted_low: float | None
    predicted_high: float | None
    observed_ratio: float
    within_range: bool | None
    became_outlier: bool


class OutcomeTracker:
    """links a made video to its card and records, at the comparison age, whether it landed inside the range."""

    def __init__(self, auto_done_days: int) -> None:
        self.auto_done_days = auto_done_days

    def measure(
        self, tracked: Iterable[TrackedCard], own: list[OwnVideo], measured: set[str]
    ) -> list[Outcome]:
        by_id = {video.video_id: video for video in own}
        outcomes = []
        for card in tracked:
            if card.stable_key in measured:
                continue
            matched_by: Literal["user", "auto"] = "user" if card.linked_video_id else "auto"
            video = by_id.get(card.linked_video_id) if card.linked_video_id else self._detect(card, own)
            if video is None or video.ratio is None or video.age_days < card.comparison_age_days:
                continue
            within = None
            if card.predicted_low is not None and card.predicted_high is not None:
                within = card.predicted_low <= video.ratio <= card.predicted_high
            outcomes.append(
                Outcome(
                    stable_key=card.stable_key,
                    video_id=video.video_id,
                    matched_by=matched_by,
                    comparison_age_days=card.comparison_age_days,
                    predicted_low=card.predicted_low,
                    predicted_high=card.predicted_high,
                    observed_ratio=round(video.ratio, 3),
                    within_range=within,
                    became_outlier=video.is_outlier,
                )
            )
        return outcomes

    def _detect(self, card: TrackedCard, own: list[OwnVideo]) -> OwnVideo | None:
        # a client upload on the same topic and format within the window after the card first appeared
        deadline = card.first_seen_at + timedelta(days=self.auto_done_days)
        matches = [
            video
            for video in own
            if card.first_seen_at <= video.published_at <= deadline
            and set(card.topic_ids) & video.topic_ids
            and (card.format_key is None or video.format_key == card.format_key)
        ]
        return min(matches, key=lambda video: video.published_at) if matches else None
