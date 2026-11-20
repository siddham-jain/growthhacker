from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.core.config import YouTubeFormatSettings
from app.core.registry import Registry
from app.domain.common import VideoFormat

Aspect = Literal["vertical", "square", "horizontal", "unknown"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class FormatInput:
    live_broadcast_content: str
    live_actual_end_at: datetime | None
    duration_s: int | None
    embed_width: int | None
    embed_height: int | None
    effective_published_at: datetime
    creator_content_type: str | None = None
    artist_channel: bool | None = None


@dataclass(frozen=True)
class FormatDecision:
    format: VideoFormat
    confidence: Confidence
    basis: list[str] = field(default_factory=list)


def aspect_of(width: int | None, height: int | None) -> Aspect:
    if not width or not height:
        return "unknown"
    ratio = height / width
    if ratio > 1.02:
        return "vertical"
    if ratio >= 0.98:
        return "square"
    return "horizontal"


class FormatRule(Protocol):
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None: ...


format_rule_registry: Registry[FormatRule] = Registry("youtube_format_rules")

CREATOR_CONTENT_TYPES = {
    "SHORTS": VideoFormat.short_form,
    "VIDEO_ON_DEMAND": VideoFormat.long_form,
    "LIVE_STREAM": VideoFormat.live,
    "STORY": VideoFormat.unknown,
}


@format_rule_registry.register("owner_analytics")
class CreatorContentTypeRule:
    # owner-authorised analytics label is authoritative for the client's own videos
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        if video.creator_content_type not in CREATOR_CONTENT_TYPES:
            return None
        basis = [f"creatorContentType={video.creator_content_type}"]
        return FormatDecision(CREATOR_CONTENT_TYPES[video.creator_content_type], "high", basis)


@format_rule_registry.register("upcoming")
class UpcomingRule:
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        if video.live_broadcast_content != "upcoming":
            return None
        return FormatDecision(VideoFormat.upcoming, "high", ["liveBroadcastContent=upcoming"])


@format_rule_registry.register("live_now")
class LiveNowRule:
    # excluded from metrics until the broadcast ends
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        if video.live_broadcast_content != "live":
            return None
        return FormatDecision(VideoFormat.unknown, "high", ["liveBroadcastContent=live", "live_now"])


@format_rule_registry.register("live_replay")
class LiveReplayRule:
    # covers lives and premieres; the api does not reliably tell them apart
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        if video.live_actual_end_at is None:
            return None
        return FormatDecision(VideoFormat.live, "high", ["liveStreamingDetails.actualEndTime"])


def shorts_limit_s(video: FormatInput, settings: YouTubeFormatSettings) -> tuple[int, Confidence]:
    published = video.effective_published_at.date()
    if published < settings.shorts_extended_from:
        return settings.legacy_shorts_max_s, "high"
    if published >= settings.artist_shorts_extended_from or video.artist_channel is False:
        return settings.shorts_max_s, "high"
    if video.artist_channel:
        return settings.legacy_shorts_max_s, "high"
    return settings.shorts_max_s, "medium"


@format_rule_registry.register("aspect_duration_date")
class AspectDurationShortsRule:
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        if video.duration_s is None:
            return None
        aspect = aspect_of(video.embed_width, video.embed_height)
        limit, confidence = shorts_limit_s(video, settings)
        basis = [f"duration={video.duration_s}s", f"aspect={aspect}", f"shorts_limit={limit}s"]
        if aspect == "unknown":
            if video.duration_s <= settings.legacy_shorts_max_s:
                return FormatDecision(VideoFormat.short_form, "low", basis)
            return FormatDecision(VideoFormat.long_form, "medium", basis)
        if aspect in ("vertical", "square") and video.duration_s <= limit:
            if confidence == "medium" and video.duration_s <= settings.legacy_shorts_max_s:
                confidence = "high"
            return FormatDecision(VideoFormat.short_form, confidence, basis)
        return FormatDecision(VideoFormat.long_form, "high", basis)


@format_rule_registry.register("fallback")
class UnknownDurationRule:
    def decide(self, video: FormatInput, settings: YouTubeFormatSettings) -> FormatDecision | None:
        return FormatDecision(VideoFormat.unknown, "low", ["duration_unknown"])


class FormatClassifier:
    """first matching rule wins; new rules are new registry entries."""

    def __init__(
        self, settings: YouTubeFormatSettings, rules: Registry[FormatRule] = format_rule_registry
    ) -> None:
        self._settings = settings
        self._rules = [rule() if isinstance(rule, type) else rule for rule in rules.all().values()]

    def classify(self, video: FormatInput) -> FormatDecision:
        for rule in self._rules:
            decision = rule.decide(video, self._settings)
            if decision is not None:
                return decision
        return FormatDecision(VideoFormat.unknown, "low", ["no_rule"])
