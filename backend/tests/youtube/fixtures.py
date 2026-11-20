"""key-free api response shapes modelled on the recorded apollo calls in plan 02."""

import string
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.youtube.errors import InvalidPageToken, NotFound
from app.adapters.youtube.mapping import channel_from_api, snapshot_from_stats, video_from_api
from app.core.clock import FrozenClock
from app.core.config import YouTubeFormatSettings
from app.domain.youtube import ChannelRecord, SearchPage, SearchQuery, VideoRecord
from app.ports.youtube import (
    CommentPage,
    NotModified,
    PlaylistItem,
    RequestContext,
    StatsBatch,
    UploadsPage,
    VideoBatch,
)
from app.services.youtube.format_classifier import FormatClassifier

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
APOLLO = "UC0InVdvqNyNzKBl1-TL348A"
APOLLO_247 = "UCdZXd2_JaNA8EBLd4fpJU3A"
# synthetic ids for database tests, so rows left by the live smoke never collide
CHANNEL_A = "UCtestChannelA000000000A"
CHANNEL_B = "UCtestChannelB000000000B"
CLASSIFIER = FormatClassifier(YouTubeFormatSettings())


def video_id(n: int) -> str:
    alphabet = string.ascii_letters + string.digits
    digits = []
    for _ in range(11):
        n, rest = divmod(n, len(alphabet))
        digits.append(alphabet[rest])
    return "".join(reversed(digits))


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def channel_item(
    channel_id: str = APOLLO,
    *,
    title: str = "Apollo Hospitals",
    handle: str = "@apollohospitalsindia",
    description: str = "Book at apollohospitals.com",
    subscribers: str | None = "1120000",
    hidden: bool = False,
    videos: int = 4328,
    country: str | None = "IN",
    keywords: str = '"best cardiology hospital" apollo',
    uploads: bool = True,
) -> dict[str, Any]:
    statistics = {"viewCount": "890000000", "hiddenSubscriberCount": hidden, "videoCount": str(videos)}
    if subscribers is not None and not hidden:
        statistics["subscriberCount"] = subscribers
    item = {
        "id": channel_id,
        "etag": "c-etag",
        "snippet": {
            "title": title,
            "description": description,
            "customUrl": handle,
            "publishedAt": "2010-03-02T10:00:00Z",
            "country": country,
            "thumbnails": {"high": {"url": f"https://yt3.ggpht.com/{channel_id}", "width": 800}},
        },
        "statistics": statistics,
        "brandingSettings": {"channel": {"keywords": keywords}},
        "status": {"madeForKids": False},
    }
    if uploads:
        item["contentDetails"] = {"relatedPlaylists": {"uploads": "UU" + channel_id[2:]}}
    return item


def video_item(
    vid: str,
    *,
    channel_id: str = APOLLO,
    published: datetime = NOW - timedelta(days=3),
    duration: str = "PT45S",
    width: int | None = 400,
    height: int | None = 711,
    views: int = 1200,
    likes: int | None = 30,
    comments: int | None = 4,
    live: str = "none",
    live_details: dict | None = None,
    title: str | None = None,
    description: str = "Book: apollohospitals.com",
    privacy: str = "public",
    default_language: str | None = "en",
    made_for_kids: bool = False,
) -> dict[str, Any]:
    statistics: dict[str, str] = {"viewCount": str(views)}
    if likes is not None:
        statistics["likeCount"] = str(likes)
    if comments is not None:
        statistics["commentCount"] = str(comments)
    item: dict[str, Any] = {
        "id": vid,
        "etag": "v-etag",
        "snippet": {
            "publishedAt": iso(published),
            "channelId": channel_id,
            "channelTitle": "Apollo Hospitals",
            "title": title or f"Knee pain explained {vid}",
            "description": description,
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/vi/{vid}/hq.jpg", "width": 480, "height": 360}
            },
            "tags": ["knee", "ortho"],
            "liveBroadcastContent": live,
            "categoryId": "28",
        },
        "contentDetails": {
            "duration": duration,
            "caption": "false",
            "licensedContent": True,
            "contentRating": {},
        },
        "status": {
            "uploadStatus": "processed",
            "privacyStatus": privacy,
            "embeddable": True,
            "publicStatsViewable": True,
            "madeForKids": made_for_kids,
        },
        "statistics": statistics,
        "player": {},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Health"]},
    }
    if default_language:
        item["snippet"]["defaultLanguage"] = default_language
    if width and height:
        item["player"] = {"embedWidth": str(width), "embedHeight": str(height)}
    if live_details:
        item["liveStreamingDetails"] = live_details
    return item


def channel_record(channel_id: str = APOLLO, *, fetched_at: datetime = NOW, **kwargs) -> ChannelRecord:
    return channel_from_api(channel_item(channel_id, **kwargs), fetched_at)


def video_record(vid: str, *, fetched_at: datetime = NOW, **kwargs) -> VideoRecord:
    return video_from_api(video_item(vid, **kwargs), fetched_at, CLASSIFIER)


class FakeGateway:
    """in-memory youtube gateway with call counting, for service tests."""

    def __init__(
        self,
        clock: FrozenClock,
        *,
        channels: Sequence[ChannelRecord] = (),
        videos: Sequence[VideoRecord] = (),
        uploads: dict[str, list[PlaylistItem]] | None = None,
        handles: dict[str, str] | None = None,
        usernames: dict[str, str] | None = None,
    ) -> None:
        self.clock = clock
        self.channels = {channel.channel_id: channel for channel in channels}
        self.videos = {video.video_id: video for video in videos}
        self.uploads = uploads or {}
        self.handles = handles or {}
        self.usernames = usernames or {}
        self.calls: list[str] = []
        self.etags: dict[str, str] = {}
        self.search_pages: dict[str, SearchPage] = {}
        self.invalid_tokens: set[str] = set()
        self.comment_pages: dict[str, CommentPage] = {}
        self.fail_stats: Exception | None = None

    async def channels_by_id(self, ids: Sequence[str], ctx: RequestContext) -> list[ChannelRecord]:
        self.calls.append("channels.list")
        return [self.channels[channel_id] for channel_id in dict.fromkeys(ids) if channel_id in self.channels]

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None:
        self.calls.append("channels.list:forHandle")
        channel_id = self.handles.get(handle.lstrip("@").casefold())
        return self.channels.get(channel_id) if channel_id else None

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None:
        self.calls.append("channels.list:forUsername")
        channel_id = self.usernames.get(username)
        return self.channels.get(channel_id) if channel_id else None

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified:
        self.calls.append("playlistItems.list")
        if playlist_id not in self.uploads:
            raise NotFound("playlistNotFound", status=404, reason="playlistNotFound")
        if page_token in self.invalid_tokens:
            self.invalid_tokens.discard(page_token)
            raise InvalidPageToken("bad token", status=400, reason="invalidPageToken")
        items = self.uploads[playlist_id]
        current_etag = f"etag-{len(items)}-{items[0].video_id if items else ''}"
        if page_token is None and etag == current_etag:
            return NotModified(etag=etag)
        start = int(page_token or 0)
        page = items[start : start + 50]
        token = str(start + 50) if start + 50 < len(items) else None
        return UploadsPage(
            playlist_id=playlist_id,
            items=page,
            next_page_token=token,
            etag=current_etag if page_token is None else None,
            fetched_at=self.clock.now(),
        )

    async def videos_by_id(
        self, ids: Sequence[str], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch:
        wanted = list(dict.fromkeys(ids))
        self.calls += ["videos.list"] * -(-len(wanted) // 50)
        found = [self.videos[i] for i in wanted if i in self.videos]
        return VideoBatch(found=found, missing_ids=[i for i in wanted if i not in self.videos])

    async def video_stats(self, ids: Sequence[str], ctx: RequestContext) -> StatsBatch:
        if self.fail_stats is not None:
            raise self.fail_stats
        wanted = list(dict.fromkeys(ids))
        self.calls += ["videos.batchGetStats"] * -(-len(wanted) // 50)
        now = self.clock.now()
        snapshots = [
            snapshot_from_stats(
                i,
                {"viewCount": self.videos[i].view_count},
                effective_published_at=self.videos[i].effective_published_at,
                captured_at=now,
                source="batch_stats",
                counting_regime="2026-11-16-coviews",
            )
            for i in wanted
            if i in self.videos
        ]
        return StatsBatch(snapshots=snapshots, missing_ids=[i for i in wanted if i not in self.videos])

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage:
        self.calls.append("search.list")
        return self.search_pages[query.q]

    async def comment_threads(self, video_id: str, max_results: int, ctx: RequestContext) -> CommentPage:
        self.calls.append("commentThreads.list")
        return self.comment_pages.get(
            video_id, CommentPage(video_id=video_id, comments_state="enabled", comments=[])
        )

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> str | None:
        if playlist_id.startswith("UU"):
            return "UC" + playlist_id[2:]
        self.calls.append("playlists.list")
        return None
