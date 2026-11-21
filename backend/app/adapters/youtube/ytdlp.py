import asyncio
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import yt_dlp
from yt_dlp.utils import DownloadError

from app.adapters.youtube.data_api_v3 import unique
from app.adapters.youtube.errors import InvalidPageToken, NotFound, ScraperFailed
from app.adapters.youtube.mapping import best_thumbnail, snapshot_from_stats, text_hash, to_int
from app.core.clock import Clock
from app.core.config import YouTubeFallbackSettings, YouTubeFreshnessSettings
from app.core.logging import get_logger
from app.domain.common import ChannelId, VideoId
from app.domain.youtube import (
    ChannelRecord,
    CommentRecord,
    SearchHit,
    SearchPage,
    SearchQuery,
    ThumbnailRef,
    VideoRecord,
)
from app.ports.youtube import (
    CommentPage,
    NotModified,
    PlaylistItem,
    RequestContext,
    StatsBatch,
    UploadsPage,
    VideoBatch,
)
from app.services.youtube.comments import redact_comment
from app.services.youtube.context import as_youtube_context
from app.services.youtube.format_classifier import FormatClassifier, FormatInput, aspect_of
from app.services.youtube.search import search_key, search_ttl

logger = get_logger(__name__)

SITE = "https://www.youtube.com"
TOKEN_PREFIX = "ytdlp:"
PAGE_SIZE = 50
# search results filtered to channels (the sp value youtube's own filter menu uses)
CHANNEL_FILTER = "EgIQAg%3D%3D"
UNAVAILABLE = re.compile(
    r"unavailable|does not exist|not available|private video|been removed|been terminated|"
    r"account associated|no longer available|http error 404",
    re.IGNORECASE,
)
LIVE_STATUS = {"is_live": "live", "is_upcoming": "upcoming"}
REPLAY_STATUSES = {"was_live", "post_live"}
PRIVACY = {"unlisted": "unlisted", "private": "private"}

Extractor = Callable[[str, dict[str, Any]], dict[str, Any]]


class _QuietLogger:
    # yt-dlp prints errors itself; they are raised and handled here instead
    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        logger.debug("ytdlp_warning", message=message[:200])

    def error(self, message: str) -> None:
        logger.debug("ytdlp_error", message=message[:200])


def extract_with_ytdlp(url: str, options: dict[str, Any]) -> dict[str, Any]:
    base = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": _QuietLogger()}
    with yt_dlp.YoutubeDL({**base, **options}) as ydl:
        info = ydl.extract_info(url, download=False)
    info = info or {}
    if "entries" in info:
        info["entries"] = [entry for entry in info["entries"] or [] if entry]
    return info


def from_timestamp(value: Any) -> datetime | None:
    seconds = to_int(value)
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def thumbnails_from_list(raw: Sequence[dict[str, Any]] | None) -> dict[str, ThumbnailRef]:
    return {
        str(item.get("id", index)): ThumbnailRef(
            url=item["url"], width=to_int(item.get("width")), height=to_int(item.get("height"))
        )
        for index, item in enumerate(raw or [])
        if item.get("url")
    }


def channel_avatar(raw: Sequence[dict[str, Any]] | None) -> str | None:
    refs = thumbnails_from_list(raw)
    if "avatar_uncropped" in refs:
        return refs["avatar_uncropped"].url
    square = [ref for ref in refs.values() if ref.width and ref.width == ref.height]
    return max(square, key=lambda ref: ref.width or 0).url if square else None


def embed_size(info: dict[str, Any], max_width: int) -> tuple[int | None, int | None]:
    # mirrors the api's player embed size (width capped at maxWidth) so the format rules apply unchanged
    width, height = to_int(info.get("width")), to_int(info.get("height"))
    if width and height:
        return max_width, round(max_width * height / width)
    ratio = info.get("aspect_ratio")
    if ratio:
        return max_width, round(max_width / float(ratio))
    if info.get("media_type") == "short":
        return round(max_width * 9 / 16), max_width
    if info.get("media_type") == "video":
        return max_width, round(max_width * 9 / 16)
    return None, None


def channel_from_ytdlp(info: dict[str, Any], fetched_at: datetime) -> ChannelRecord:
    channel_id = info["channel_id"]
    followers = to_int(info.get("channel_follower_count"))
    return ChannelRecord(
        channel_id=channel_id,
        title=info.get("channel") or info.get("uploader") or "",
        handle=info.get("uploader_id") if str(info.get("uploader_id") or "").startswith("@") else None,
        description=info.get("description") or "",
        country=None,
        default_language=None,
        published_at=fetched_at,
        uploads_playlist_id="UU" + channel_id[2:],
        subscriber_count=followers,
        hidden_subscriber_count=False,
        # channel totals are not on the page; the store keeps the last api values
        view_count=0,
        video_count=0,
        keywords=[str(tag) for tag in info.get("tags") or []],
        thumbnail_url=channel_avatar(info.get("thumbnails")),
        data_source="yt_dlp",
        metadata_fetched_at=fetched_at,
        stats_fetched_at=fetched_at,
    )


def video_from_ytdlp(
    info: dict[str, Any], fetched_at: datetime, classifier: FormatClassifier, embed_max_width: int
) -> VideoRecord:
    published_at = from_timestamp(info.get("timestamp")) or fetched_at
    status = info.get("live_status")
    live_content = LIVE_STATUS.get(status, "none")
    duration = to_int(info.get("duration"))
    release = from_timestamp(info.get("release_timestamp"))
    actual_start = release if status in REPLAY_STATUSES or status == "is_live" else None
    # yt-dlp has no end time, so a replay ends one duration after it started
    actual_end = (
        (actual_start or published_at) + timedelta(seconds=duration or 0)
        if status in REPLAY_STATUSES
        else None
    )
    effective = actual_start or published_at
    width, height = embed_size(info, embed_max_width)
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
    basis = [*decision.basis, f"ytdlp_media_type={info.get('media_type')}"]
    thumbnails = thumbnails_from_list(info.get("thumbnails"))
    like_count = to_int(info.get("like_count"))
    comment_count = to_int(info.get("comment_count"))
    title, description = info.get("title") or "", info.get("description") or ""
    captions = {key for key in info.get("subtitles") or {} if key != "live_chat"}
    return VideoRecord(
        video_id=info["id"],
        channel_id=info["channel_id"],
        channel_title=info.get("channel") or info.get("uploader") or "",
        published_at=published_at,
        effective_published_at=effective,
        live_broadcast_content=live_content,
        live_actual_start_at=actual_start,
        live_actual_end_at=actual_end,
        live_scheduled_start_at=release if status == "is_upcoming" else None,
        title=title,
        description=description,
        tags=[str(tag) for tag in info.get("tags") or []],
        content_hash=text_hash(title, description),
        description_hash=text_hash(description),
        default_language=info.get("language"),
        duration_s=duration,
        embed_width=width,
        embed_height=height,
        aspect=aspect_of(width, height),
        format=decision.format,
        format_basis=basis,
        format_confidence=decision.confidence,
        has_captions=bool(captions),
        age_restricted=(to_int(info.get("age_limit")) or 0) >= 18,
        privacy_status=PRIVACY.get(info.get("availability"), "public"),
        embeddable=info.get("playable_in_embed") is not False,
        thumbnails=thumbnails,
        thumbnail_url=info.get("thumbnail") or best_thumbnail(thumbnails),
        comments_state="enabled" if comment_count is not None else "unknown",
        likes_visible=like_count is not None,
        view_count=to_int(info.get("view_count")) or 0,
        like_count=like_count,
        comment_count=comment_count,
        ingest_source="manual",
        data_source="yt_dlp",
        metadata_fetched_at=fetched_at,
    )


def comments_from_ytdlp(
    raw: Sequence[dict[str, Any]], *, video_id: str, fetched_at: datetime, max_threads: int
) -> list[CommentRecord]:
    """top-level comments plus the channel owner's replies; author fields are read only to spot the owner."""
    top = [item for item in raw if item.get("parent") == "root"][:max_threads]
    replies: dict[str, list[dict[str, Any]]] = {}
    for item in raw:
        if item.get("parent") not in (None, "root"):
            replies.setdefault(item["parent"], []).append(item)

    def record(item: dict[str, Any], rank: int, **extra: Any) -> CommentRecord:
        return CommentRecord(
            comment_id=item["id"],
            video_id=video_id,
            text=redact_comment(item.get("text") or ""),
            like_count=to_int(item.get("like_count")) or 0,
            published_at=from_timestamp(item.get("timestamp")) or fetched_at,
            relevance_rank=rank,
            fetched_at=fetched_at,
            **extra,
        )

    records = []
    for rank, item in enumerate(top):
        thread = replies.get(item["id"], [])
        owner_replies = [reply for reply in thread if reply.get("author_is_uploader")]
        records.append(
            record(
                item,
                rank,
                reply_count=len(thread),
                is_channel_owner_reply=bool(item.get("author_is_uploader")),
                owner_replied=bool(owner_replies),
            )
        )
        records += [
            record(reply, rank, reply_count=0, is_channel_owner_reply=True) for reply in owner_replies
        ]
    return records


def page_offset(page_token: str | None) -> int:
    if page_token is None:
        return 0
    if not page_token.startswith(TOKEN_PREFIX):
        raise InvalidPageToken("page token is not from the yt-dlp fallback", reason="invalidPageToken")
    try:
        return max(0, int(page_token.removeprefix(TOKEN_PREFIX)))
    except ValueError:
        raise InvalidPageToken("malformed yt-dlp page token", reason="invalidPageToken") from None


class YtDlpGateway:
    """scrapes youtube.com with yt-dlp; only used when the data api cannot answer (see FallbackGateway)."""

    def __init__(
        self,
        classifier: FormatClassifier,
        clock: Clock,
        *,
        settings: YouTubeFallbackSettings,
        freshness: YouTubeFreshnessSettings,
        counting_regime: Callable[[datetime], str],
        embed_max_width: int = 400,
        extractor: Extractor = extract_with_ytdlp,
    ) -> None:
        self._classifier = classifier
        self._clock = clock
        self._settings = settings
        self._freshness = freshness
        self._counting_regime = counting_regime
        self._embed_max_width = embed_max_width
        self._extractor = extractor
        self._gate = asyncio.Semaphore(settings.concurrency)

    async def _extract(self, url: str, **options: Any) -> dict[str, Any]:
        """raises NotFound for content youtube says is gone and ScraperFailed for everything else."""
        options.setdefault("socket_timeout", self._settings.socket_timeout_s)
        options.setdefault("js_runtimes", {name: {} for name in self._settings.js_runtimes})
        async with self._gate:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._extractor, url, options), self._settings.timeout_s
                )
            except DownloadError as error:
                message = str(error)
                if UNAVAILABLE.search(message):
                    raise NotFound(message[:300], reason="ytdlpUnavailable") from None
                raise ScraperFailed(message[:300], reason="ytdlpFailed") from None
            except TimeoutError:
                raise ScraperFailed(f"yt-dlp timed out on {url}", reason="ytdlpTimeout") from None

    async def _channel(self, url: str) -> ChannelRecord | None:
        try:
            info = await self._extract(url, extract_flat=True, playlistend=1)
        except NotFound:
            return None
        if not str(info.get("channel_id") or "").startswith("UC"):
            return None
        return channel_from_ytdlp(info, self._clock.now())

    async def channels_by_id(self, ids: Sequence[ChannelId], ctx: RequestContext) -> list[ChannelRecord]:
        found = await asyncio.gather(*(self._channel(f"{SITE}/channel/{cid}/about") for cid in unique(ids)))
        return [record for record in found if record is not None]

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None:
        return await self._channel(f"{SITE}/@{quote_plus(handle.strip().lstrip('@'))}/about")

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None:
        return await self._channel(f"{SITE}/user/{quote_plus(username.strip())}/about")

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified:
        offset = page_offset(page_token)
        info = await self._extract(
            f"{SITE}/playlist?list={playlist_id}",
            extract_flat="in_playlist",
            playliststart=offset + 1,
            playlistend=offset + PAGE_SIZE,
            # dates in a flat listing come from "3 days ago" labels, so they are day-accurate at best
            extractor_args={"youtubetab": {"approximate_date": [""]}},
        )
        entries = info.get("entries") or []
        items = [
            PlaylistItem(video_id=entry["id"], published_at=published)
            for entry in entries
            if entry.get("id") and (published := from_timestamp(entry.get("timestamp")))
        ]
        total = to_int(info.get("playlist_count"))
        more = len(entries) == PAGE_SIZE and (total is None or offset + PAGE_SIZE < total)
        return UploadsPage(
            playlist_id=playlist_id,
            items=items,
            next_page_token=f"{TOKEN_PREFIX}{offset + PAGE_SIZE}" if more else None,
            etag=None,
            fetched_at=self._clock.now(),
        )

    async def _video_info(self, video_id: str) -> dict[str, Any] | None:
        try:
            info = await self._extract(f"{SITE}/watch?v={video_id}")
        except NotFound:
            return None
        return info if info.get("id") == video_id and info.get("channel_id") else None

    async def videos_by_id(
        self, ids: Sequence[VideoId], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch:
        requested = unique(ids)
        infos = await asyncio.gather(*(self._video_info(video_id) for video_id in requested))
        fetched_at = self._clock.now()
        found = [
            video_from_ytdlp(info, fetched_at, self._classifier, self._embed_max_width)
            for info in infos
            if info is not None
        ]
        seen = {video.video_id for video in found}
        return VideoBatch(
            found=found, missing_ids=[video_id for video_id in requested if video_id not in seen]
        )

    async def video_stats(self, ids: Sequence[VideoId], ctx: RequestContext) -> StatsBatch:
        requested = unique(ids)
        infos = await asyncio.gather(*(self._video_info(video_id) for video_id in requested))
        captured_at = self._clock.now()
        snapshots = [
            snapshot_from_stats(
                info["id"],
                {
                    "viewCount": info.get("view_count"),
                    "likeCount": info.get("like_count"),
                    "commentCount": info.get("comment_count"),
                },
                effective_published_at=from_timestamp(info.get("release_timestamp"))
                or from_timestamp(info.get("timestamp"))
                or captured_at,
                captured_at=captured_at,
                source="yt_dlp",
                counting_regime=self._counting_regime(captured_at),
            )
            for info in infos
            if info is not None
        ]
        seen = {snapshot.video_id for snapshot in snapshots}
        return StatsBatch(
            snapshots=snapshots, missing_ids=[video_id for video_id in requested if video_id not in seen]
        )

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage:
        # date, region and duration filters have no scraper equivalent, so results are unfiltered
        if query.type == "channel":
            url = f"{SITE}/results?search_query={quote_plus(query.q)}&sp={CHANNEL_FILTER}"
            info = await self._extract(url, extract_flat=True, playlistend=query.max_results)
        else:
            prefix = "ytsearchdate" if query.order == "date" else "ytsearch"
            info = await self._extract(f"{prefix}{query.max_results}:{query.q}", extract_flat=True)
        fetched_at = self._clock.now()
        hits = []
        for entry in info.get("entries") or []:
            channel_id = entry.get("channel_id")
            resource_id = channel_id if query.type == "channel" else entry.get("id")
            if channel_id and resource_id:
                hits.append(SearchHit(kind=query.type, id=resource_id, channel_id=channel_id, rank=len(hits)))
        # an empty scrape is often a soft block, so it expires at once and the next call retries
        ttl = (
            search_ttl(query, as_youtube_context(ctx).pool, self._freshness, fetched_at.date())
            if hits
            else timedelta(0)
        )
        return SearchPage(
            query_key=search_key(query),
            items=hits,
            next_page_token=None,
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl,
            from_cache=False,
        )

    async def comment_threads(self, video_id: VideoId, max_results: int, ctx: RequestContext) -> CommentPage:
        threads = max(1, min(100, max_results))
        replies = self._settings.max_comment_replies
        info = await self._extract(
            f"{SITE}/watch?v={video_id}",
            getcomments=True,
            extractor_args={
                "youtube": {
                    "max_comments": [str(threads * (replies + 1)), str(threads), "all", str(replies)],
                    "comment_sort": ["top"],
                }
            },
        )
        raw = info.get("comments")
        if not raw:
            state = "disabled" if raw is None and info.get("comment_count") is None else "enabled"
            return CommentPage(video_id=video_id, comments_state=state, comments=[])
        comments = comments_from_ytdlp(
            raw, video_id=video_id, fetched_at=self._clock.now(), max_threads=threads
        )
        return CommentPage(video_id=video_id, comments_state="enabled", comments=comments)

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> ChannelId | None:
        if playlist_id.startswith("UU") and len(playlist_id) == 24:
            return "UC" + playlist_id[2:]
        try:
            info = await self._extract(
                f"{SITE}/playlist?list={playlist_id}", extract_flat=True, playlistend=1
            )
        except NotFound:
            return None
        return info.get("channel_id")
