import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from pydantic import SecretStr

from app.adapters.youtube import fields
from app.adapters.youtube.errors import CommentsDisabled
from app.adapters.youtube.http import ApiRequest, ApiResponse, RequestHandler
from app.adapters.youtube.mapping import (
    channel_from_api,
    comments_from_thread,
    parse_time,
    playlist_item_from_api,
    search_hit_from_api,
    snapshot_from_stats,
    video_from_api,
)
from app.core.clock import Clock
from app.core.config import YouTubeFreshnessSettings
from app.domain.common import ChannelId, VideoId
from app.domain.youtube import ChannelRecord, SearchPage, SearchQuery
from app.ports.youtube import CommentPage, NotModified, RequestContext, StatsBatch, UploadsPage, VideoBatch
from app.services.youtube.comments import redact_comment
from app.services.youtube.context import as_youtube_context
from app.services.youtube.format_classifier import FormatClassifier
from app.services.youtube.search import published_bound, search_key, search_ttl

MAX_IDS = 50


def chunks[T](values: Sequence[T], size: int = MAX_IDS) -> Iterable[list[T]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


class DataApiV3Gateway:
    """youtube data api v3 with an api key; maps responses to domain records and drops category signals."""

    def __init__(
        self,
        handler: RequestHandler,
        classifier: FormatClassifier,
        clock: Clock,
        *,
        base_url: str,
        freshness: YouTubeFreshnessSettings,
        counting_regime: Callable[[datetime], str],
        embed_max_width: int = 400,
    ) -> None:
        self._handler = handler
        self._classifier = classifier
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._freshness = freshness
        self._counting_regime = counting_regime
        self._embed_max_width = embed_max_width

    async def _get(
        self,
        endpoint: str,
        path: str,
        params: dict[str, str],
        ctx: RequestContext,
        *,
        etag: str | None = None,
        bearer: SecretStr | None = None,
    ) -> ApiResponse:
        request = ApiRequest(
            endpoint=endpoint,
            url=f"{self._base_url}/{path}",
            params=params,
            ctx=ctx,
            etag=etag,
            bearer=bearer,
        )
        return await self._handler.send(request)

    async def _channels(self, params: dict[str, str], ctx: RequestContext) -> list[ChannelRecord]:
        params = {"part": fields.CHANNEL_PARTS, "fields": fields.CHANNEL_FIELDS, **params}
        response = await self._get("channels.list", "channels", params, ctx)
        fetched_at = self._clock.now()
        return [channel_from_api(item, fetched_at) for item in response.body.get("items") or []]

    async def channels_by_id(self, ids: Sequence[ChannelId], ctx: RequestContext) -> list[ChannelRecord]:
        records: list[ChannelRecord] = []
        for batch in chunks(unique(ids)):
            records += await self._channels({"id": ",".join(batch), "maxResults": str(MAX_IDS)}, ctx)
        return records

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None:
        normalized = unicodedata.normalize("NFC", handle.strip()).lstrip("@")
        found = await self._channels({"forHandle": f"@{normalized}"}, ctx)
        return found[0] if found else None

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None:
        found = await self._channels({"forUsername": username.strip()}, ctx)
        return found[0] if found else None

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified:
        params = {
            "playlistId": playlist_id,
            "part": fields.PLAYLIST_ITEM_PARTS,
            "fields": fields.PLAYLIST_ITEM_FIELDS,
            "maxResults": str(MAX_IDS),
        }
        if page_token:
            params["pageToken"] = page_token
        response = await self._get("playlistItems.list", "playlistItems", params, ctx, etag=etag)
        if response.not_modified:
            return NotModified(etag=etag or "")
        items = []
        for raw in response.body.get("items") or []:
            item, privacy = playlist_item_from_api(raw)
            if item is not None and privacy == "public":
                items.append(item)
        return UploadsPage(
            playlist_id=playlist_id,
            items=items,
            next_page_token=response.body.get("nextPageToken"),
            etag=response.body.get("etag"),
            fetched_at=self._clock.now(),
        )

    async def videos_by_id(
        self, ids: Sequence[VideoId], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch:
        requested = unique(ids)
        part = fields.VIDEO_PARTS + (",localizations" if with_localizations else "")
        mask = fields.VIDEO_FIELDS_LOCALIZED if with_localizations else fields.VIDEO_FIELDS
        found = []
        for batch in chunks(requested):
            params = {
                "id": ",".join(batch),
                "part": part,
                "fields": mask,
                "maxWidth": str(self._embed_max_width),
                "maxResults": str(MAX_IDS),
            }
            response = await self._get("videos.list", "videos", params, ctx)
            fetched_at = self._clock.now()
            found += [
                video_from_api(item, fetched_at, self._classifier)
                for item in response.body.get("items") or []
            ]
        seen = {video.video_id for video in found}
        return VideoBatch(
            found=found, missing_ids=[video_id for video_id in requested if video_id not in seen]
        )

    async def video_stats(self, ids: Sequence[VideoId], ctx: RequestContext) -> StatsBatch:
        requested = unique(ids)
        snapshots = []
        for batch in chunks(requested):
            params = {"id": ",".join(batch), "part": fields.BATCH_STATS_PARTS}
            response = await self._get("videos.batchGetStats", "videos:batchGetStats", params, ctx)
            captured_at = self._clock.now()
            for item in response.body.get("items") or []:
                snippet = item.get("snippet") or {}
                published = (
                    parse_time(snippet.get("publishTime") or snippet.get("publishedAt")) or captured_at
                )
                snapshots.append(
                    snapshot_from_stats(
                        item["id"],
                        item.get("statistics") or {},
                        effective_published_at=published,
                        captured_at=captured_at,
                        source="batch_stats",
                        counting_regime=self._counting_regime(captured_at),
                    )
                )
        seen = {snapshot.video_id for snapshot in snapshots}
        return StatsBatch(
            snapshots=snapshots, missing_ids=[video_id for video_id in requested if video_id not in seen]
        )

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage:
        params = {
            "part": "snippet",
            "fields": fields.SEARCH_FIELDS,
            "q": query.q,
            "type": query.type,
            "order": query.order,
            "maxResults": str(query.max_results),
        }
        optional = {
            "publishedAfter": published_bound(query.published_after),
            "publishedBefore": published_bound(query.published_before),
            "regionCode": query.region_code,
            "relevanceLanguage": query.relevance_language,
            "videoDuration": query.video_duration
            if query.type == "video" and query.video_duration != "any"
            else None,
            "pageToken": page_token,
        }
        params.update({name: value for name, value in optional.items() if value})
        response = await self._get("search.list", "search", params, ctx)
        fetched_at = self._clock.now()
        hits = [search_hit_from_api(item, rank) for rank, item in enumerate(response.body.get("items") or [])]
        # the api may mix kinds; keep only what was asked for
        items = [hit for hit in hits if hit is not None and hit.kind == query.type]
        ttl = search_ttl(query, as_youtube_context(ctx).pool, self._freshness, fetched_at.date())
        return SearchPage(
            query_key=search_key(query),
            items=items,
            next_page_token=response.body.get("nextPageToken"),
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl,
            from_cache=False,
        )

    async def comment_threads(self, video_id: VideoId, max_results: int, ctx: RequestContext) -> CommentPage:
        params = {
            "videoId": video_id,
            "part": fields.COMMENT_PARTS,
            "fields": fields.COMMENT_FIELDS,
            "order": "relevance",
            "textFormat": "plainText",
            "maxResults": str(max(1, min(100, max_results))),
        }
        try:
            response = await self._get("commentThreads.list", "commentThreads", params, ctx)
        except CommentsDisabled:
            return CommentPage(video_id=video_id, comments_state="disabled", comments=[])
        fetched_at = self._clock.now()
        comments = []
        for rank, thread in enumerate(response.body.get("items") or []):
            comments += comments_from_thread(
                thread, video_id=video_id, rank=rank, fetched_at=fetched_at, redact=redact_comment
            )
        return CommentPage(video_id=video_id, comments_state="enabled", comments=comments)

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> ChannelId | None:
        if playlist_id.startswith("UU") and len(playlist_id) == 24:
            return "UC" + playlist_id[2:]
        params = {"id": playlist_id, "part": "snippet", "fields": fields.PLAYLIST_FIELDS}
        response = await self._get("playlists.list", "playlists", params, ctx)
        items = response.body.get("items") or []
        return (items[0].get("snippet") or {}).get("channelId") if items else None

    async def own_channel_id(self, access_token: SecretStr, ctx: RequestContext) -> ChannelId | None:
        params = {"part": "id", "mine": "true", "fields": "items(id)"}
        response = await self._get("channels.list", "channels", params, ctx, bearer=access_token)
        items = response.body.get("items") or []
        return items[0]["id"] if items else None
