import hashlib
import re
import shlex
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.domain.youtube import (
    ChannelRecord,
    CommentRecord,
    LocalizedText,
    RegionRestriction,
    SearchHit,
    ThumbnailRef,
    VideoRecord,
    VideoSnapshot,
)
from app.ports.youtube import PlaylistItem
from app.services.youtube.format_classifier import FormatClassifier, FormatInput, aspect_of

DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
THUMBNAIL_PREFERENCE = ("maxres", "standard", "high", "medium", "default")


def parse_duration(value: str | None) -> int | None:
    # upcoming broadcasts report P0D, which means unknown rather than zero seconds
    if not value:
        return None
    match = DURATION.match(value)
    if match is None:
        return None
    parts = {name: float(amount) for name, amount in match.groupdict().items() if amount}
    seconds = int(
        parts.get("days", 0) * 86400
        + parts.get("hours", 0) * 3600
        + parts.get("minutes", 0) * 60
        + parts.get("seconds", 0)
    )
    return seconds or None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def text_hash(*parts: str) -> str:
    normalized = "\n".join(unicodedata.normalize("NFC", part or "").strip() for part in parts)
    return hashlib.sha256(normalized.encode()).hexdigest()


def thumbnails_of(raw: dict[str, Any] | None) -> dict[str, ThumbnailRef]:
    refs = {}
    for key, value in (raw or {}).items():
        if isinstance(value, dict) and value.get("url"):
            refs[key] = ThumbnailRef(
                url=value["url"], width=to_int(value.get("width")), height=to_int(value.get("height"))
            )
    return refs


def best_thumbnail(refs: dict[str, ThumbnailRef]) -> str | None:
    for key in THUMBNAIL_PREFERENCE:
        if key in refs:
            return refs[key].url
    return next(iter(refs.values())).url if refs else None


def split_keywords(value: str | None) -> list[str]:
    # brandingSettings keywords are space separated with quoted phrases kept whole
    if not value:
        return []
    try:
        return [token for token in shlex.split(value) if token]
    except ValueError:
        return [token.strip('"') for token in value.split() if token.strip('"')]


def channel_from_api(item: dict[str, Any], fetched_at: datetime) -> ChannelRecord:
    snippet = item.get("snippet") or {}
    statistics = item.get("statistics") or {}
    branding = (item.get("brandingSettings") or {}).get("channel") or {}
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    hidden = bool(statistics.get("hiddenSubscriberCount"))
    return ChannelRecord(
        channel_id=item["id"],
        title=snippet.get("title") or "",
        handle=snippet.get("customUrl"),
        description=snippet.get("description") or branding.get("description") or "",
        country=snippet.get("country") or branding.get("country"),
        default_language=snippet.get("defaultLanguage") or branding.get("defaultLanguage"),
        published_at=parse_time(snippet.get("publishedAt")) or fetched_at,
        uploads_playlist_id=uploads,
        subscriber_count=None if hidden else to_int(statistics.get("subscriberCount")),
        hidden_subscriber_count=hidden,
        view_count=to_int(statistics.get("viewCount")) or 0,
        video_count=to_int(statistics.get("videoCount")) or 0,
        keywords=split_keywords(branding.get("keywords")),
        thumbnail_url=best_thumbnail(thumbnails_of(snippet.get("thumbnails"))),
        made_for_kids=(item.get("status") or {}).get("madeForKids"),
        metadata_fetched_at=fetched_at,
        stats_fetched_at=fetched_at,
    )


def _region_restriction(raw: dict[str, Any] | None) -> RegionRestriction | None:
    if not raw:
        return None
    return RegionRestriction(allowed=raw.get("allowed"), blocked=raw.get("blocked"))


def _privacy(value: str | None) -> str:
    return value if value in ("public", "unlisted", "private") else "public"


def _comments_state(statistics: dict[str, Any]) -> str:
    if not statistics:
        return "unknown"
    return "enabled" if "commentCount" in statistics else "disabled"


def video_from_api(item: dict[str, Any], fetched_at: datetime, classifier: FormatClassifier) -> VideoRecord:
    snippet = item.get("snippet") or {}
    details = item.get("contentDetails") or {}
    status = item.get("status") or {}
    statistics = item.get("statistics") or {}
    player = item.get("player") or {}
    live = item.get("liveStreamingDetails") or {}
    placement = item.get("paidProductPlacementDetails") or {}
    published_at = parse_time(snippet.get("publishedAt")) or fetched_at
    actual_start = parse_time(live.get("actualStartTime"))
    actual_end = parse_time(live.get("actualEndTime"))
    effective = actual_start or published_at
    live_content = snippet.get("liveBroadcastContent")
    live_content = live_content if live_content in ("none", "live", "upcoming") else "none"
    duration = parse_duration(details.get("duration"))
    width, height = to_int(player.get("embedWidth")), to_int(player.get("embedHeight"))
    decision = classifier.classify(
        FormatInput(
            live_broadcast_content=live_content,
            live_actual_end_at=actual_end,
            duration_s=duration,
            embed_width=width,
            embed_height=height,
            effective_published_at=effective,
        )
    )
    like_count = to_int(statistics.get("likeCount"))
    comment_count = to_int(statistics.get("commentCount"))
    thumbnails = thumbnails_of(snippet.get("thumbnails"))
    localizations = {
        language: LocalizedText(title=value.get("title") or "", description=value.get("description") or "")
        for language, value in (item.get("localizations") or {}).items()
        if isinstance(value, dict)
    }
    title, description = snippet.get("title") or "", snippet.get("description") or ""
    return VideoRecord(
        video_id=item["id"],
        channel_id=snippet["channelId"],
        channel_title=snippet.get("channelTitle") or "",
        published_at=published_at,
        effective_published_at=effective,
        live_broadcast_content=live_content,
        live_actual_start_at=actual_start,
        live_actual_end_at=actual_end,
        live_scheduled_start_at=parse_time(live.get("scheduledStartTime")),
        title=title,
        description=description,
        tags=list(snippet.get("tags") or []),
        content_hash=text_hash(title, description),
        description_hash=text_hash(description),
        default_language=snippet.get("defaultLanguage"),
        default_audio_language=snippet.get("defaultAudioLanguage"),
        localizations=localizations,
        duration_s=duration,
        embed_width=width,
        embed_height=height,
        aspect=aspect_of(width, height),
        format=decision.format,
        format_basis=decision.basis,
        format_confidence=decision.confidence,
        has_captions=str(details.get("caption")).lower() == "true",
        licensed_content=bool(details.get("licensedContent")),
        age_restricted=(details.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted",
        region_restriction=_region_restriction(details.get("regionRestriction")),
        made_for_kids=bool(status.get("madeForKids")),
        privacy_status=_privacy(status.get("privacyStatus")),
        upload_status=status.get("uploadStatus") or "processed",
        embeddable=status.get("embeddable", True),
        public_stats_viewable=status.get("publicStatsViewable", True),
        has_paid_product_placement=placement.get("hasPaidProductPlacement"),
        thumbnails=thumbnails,
        thumbnail_url=best_thumbnail(thumbnails),
        comments_state=_comments_state(statistics),
        likes_visible=like_count is not None,
        view_count=to_int(statistics.get("viewCount")) or 0,
        like_count=like_count,
        comment_count=comment_count,
        ingest_source="manual",
        metadata_fetched_at=fetched_at,
    )


def snapshot_from_stats(
    video_id: str,
    statistics: dict[str, Any],
    *,
    effective_published_at: datetime,
    captured_at: datetime,
    source: str,
    counting_regime: str,
) -> VideoSnapshot:
    age_hours = max(0.0, (captured_at - effective_published_at).total_seconds() / 3600)
    return VideoSnapshot(
        video_id=video_id,
        slot_at=captured_at.replace(minute=0, second=0, microsecond=0),
        captured_at=captured_at,
        age_hours=age_hours,
        view_count=to_int(statistics.get("viewCount")) or 0,
        like_count=to_int(statistics.get("likeCount")),
        comment_count=to_int(statistics.get("commentCount")),
        source=source,
        counting_regime=counting_regime,
    )


def playlist_item_from_api(item: dict[str, Any]) -> tuple[PlaylistItem | None, str]:
    details = item.get("contentDetails") or {}
    privacy = (item.get("status") or {}).get("privacyStatus") or "public"
    video_id = details.get("videoId") or ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId")
    published = parse_time(details.get("videoPublishedAt"))
    if not video_id or published is None:
        return None, privacy
    return PlaylistItem(video_id=video_id, published_at=published), privacy


def search_hit_from_api(item: dict[str, Any], rank: int) -> SearchHit | None:
    identity = item.get("id") or {}
    kind = str(identity.get("kind", "")).removeprefix("youtube#")
    channel_id = identity.get("channelId") or (item.get("snippet") or {}).get("channelId")
    resource_id = identity.get("videoId") if kind == "video" else identity.get("channelId")
    if kind not in ("video", "channel") or not resource_id or not channel_id:
        return None
    return SearchHit(kind=kind, id=resource_id, channel_id=channel_id, rank=rank)


def comments_from_thread(
    thread: dict[str, Any],
    *,
    video_id: str,
    rank: int,
    fetched_at: datetime,
    redact: Callable[[str], str],
) -> list[CommentRecord]:
    """top-level comment plus the channel owner's own replies; author ids are read only to spot the owner."""
    snippet = thread.get("snippet") or {}
    channel_id = snippet.get("channelId") or ""
    top = snippet.get("topLevelComment") or {}
    top_snippet = top.get("snippet") or {}
    text = top_snippet.get("textOriginal")
    published = parse_time(top_snippet.get("publishedAt"))
    if not top.get("id") or text is None or published is None:
        return []

    def author(item_snippet: dict[str, Any]) -> str | None:
        return (item_snippet.get("authorChannelId") or {}).get("value")

    owner_replies = [
        reply
        for reply in (thread.get("replies") or {}).get("comments") or []
        if channel_id and author(reply.get("snippet") or {}) == channel_id
    ]
    records = [
        CommentRecord(
            comment_id=top["id"],
            video_id=video_id,
            text=redact(text),
            like_count=to_int(top_snippet.get("likeCount")) or 0,
            reply_count=to_int(snippet.get("totalReplyCount")) or 0,
            published_at=published,
            relevance_rank=rank,
            is_channel_owner_reply=bool(channel_id) and author(top_snippet) == channel_id,
            owner_replied=bool(owner_replies),
            fetched_at=fetched_at,
        )
    ]
    for reply in owner_replies:
        reply_snippet = reply.get("snippet") or {}
        reply_published = parse_time(reply_snippet.get("publishedAt"))
        if not reply.get("id") or reply_published is None:
            continue
        records.append(
            CommentRecord(
                comment_id=reply["id"],
                video_id=video_id,
                text=redact(reply_snippet.get("textOriginal") or ""),
                like_count=to_int(reply_snippet.get("likeCount")) or 0,
                reply_count=0,
                published_at=reply_published,
                relevance_rank=rank,
                is_channel_owner_reply=True,
                fetched_at=fetched_at,
            )
        )
    return records
