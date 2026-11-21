from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import SecretStr

from app.adapters.youtube.data_api_v3 import DataApiV3Gateway
from app.adapters.youtube.errors import (
    CircuitOpen,
    GoogleQuotaExceeded,
    KeyRejected,
    NotSent,
    Transient,
)
from app.adapters.youtube.ytdlp import TOKEN_PREFIX, YtDlpGateway
from app.core.logging import get_logger
from app.domain.common import ChannelId, VideoId
from app.domain.youtube import ChannelRecord, SearchPage, SearchQuery
from app.ports.quota import SOURCE_DOWN_REASONS, QuotaExhausted
from app.ports.youtube import CommentPage, NotModified, RequestContext, StatsBatch, UploadsPage, VideoBatch

logger = get_logger(__name__)

# the data api is not answering at all; a missing key surfaces as KeyRejected(reason="keyMissing")
PRIMARY_DOWN = (KeyRejected, GoogleQuotaExceeded, CircuitOpen, Transient, NotSent)


def primary_down(error: Exception) -> bool:
    # run, client, priority and search-pool caps are deliberate limits, so they are never scraped around
    if isinstance(error, QuotaExhausted):
        return error.reason in SOURCE_DOWN_REASONS
    return isinstance(error, PRIMARY_DOWN)


class FallbackGateway:
    """data api first; yt-dlp only when the api is down, the project is out of quota or the key is rejected."""

    def __init__(self, primary: DataApiV3Gateway, fallback: YtDlpGateway, *, enabled: bool) -> None:
        self._primary = primary
        self._fallback = fallback
        self._enabled = enabled

    async def _call[T](
        self, endpoint: str, primary: Callable[[], Awaitable[T]], fallback: Callable[[], Awaitable[T]]
    ) -> T:
        try:
            return await primary()
        except (*PRIMARY_DOWN, QuotaExhausted) as error:
            if not self._enabled or not primary_down(error):
                raise
            logger.warning("youtube_fallback_ytdlp", endpoint=endpoint, error=type(error).__name__)
            return await fallback()

    def _both(self, name: str, *args: Any, **kwargs: Any):
        return self._call(
            name,
            lambda: getattr(self._primary, name)(*args, **kwargs),
            lambda: getattr(self._fallback, name)(*args, **kwargs),
        )

    async def channels_by_id(self, ids: Sequence[ChannelId], ctx: RequestContext) -> list[ChannelRecord]:
        return await self._both("channels_by_id", ids, ctx)

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None:
        return await self._both("channel_by_handle", handle, ctx)

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None:
        return await self._both("channel_by_username", username, ctx)

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified:
        # a scraper page token would cost an api unit just to be rejected, so it stays on the scraper
        if self._enabled and page_token and page_token.startswith(TOKEN_PREFIX):
            return await self._fallback.uploads_page(playlist_id, page_token, ctx, etag)
        return await self._call(
            "uploads_page",
            lambda: self._primary.uploads_page(playlist_id, page_token, ctx, etag),
            lambda: self._fallback.uploads_page(playlist_id, page_token, ctx, etag),
        )

    async def videos_by_id(
        self, ids: Sequence[VideoId], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch:
        return await self._both("videos_by_id", ids, ctx, with_localizations)

    async def video_stats(self, ids: Sequence[VideoId], ctx: RequestContext) -> StatsBatch:
        return await self._both("video_stats", ids, ctx)

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage:
        return await self._both("search", query, page_token, ctx)

    async def comment_threads(self, video_id: VideoId, max_results: int, ctx: RequestContext) -> CommentPage:
        return await self._both("comment_threads", video_id, max_results, ctx)

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> ChannelId | None:
        return await self._both("playlist_owner", playlist_id, ctx)

    async def own_channel_id(self, access_token: SecretStr, ctx: RequestContext) -> ChannelId | None:
        # owner oauth has no scraper equivalent
        return await self._primary.own_channel_id(access_token, ctx)
