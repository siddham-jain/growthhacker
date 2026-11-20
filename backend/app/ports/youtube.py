from collections.abc import Sequence
from datetime import date
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, SecretStr

from app.domain.common import ChannelId, DomainModel, VideoId
from app.domain.youtube import (
    ChannelRecord,
    CommentRecord,
    QuotaPriority,
    SearchPage,
    SearchQuery,
    VideoRecord,
    VideoSnapshot,
)


class RequestContext(DomainModel):
    # carried by every gateway call for quota attribution
    client_id: UUID | None
    run_id: UUID | None
    priority: QuotaPriority


class PlaylistItem(DomainModel):
    video_id: VideoId
    published_at: AwareDatetime


class UploadsPage(DomainModel):
    playlist_id: str
    items: list[PlaylistItem]
    next_page_token: str | None
    etag: str | None
    fetched_at: AwareDatetime


class NotModified(DomainModel):
    etag: str


class VideoBatch(DomainModel):
    found: list[VideoRecord]
    missing_ids: list[VideoId] = []


class StatsBatch(DomainModel):
    snapshots: list[VideoSnapshot]
    missing_ids: list[VideoId] = []


class CommentPage(DomainModel):
    video_id: VideoId
    comments_state: Literal["enabled", "disabled", "unknown"]
    comments: list[CommentRecord]


class YouTubeGateway(Protocol):
    # official Data API v3 only; topicDetails is never requested and categoryId is dropped
    async def channels_by_id(self, ids: Sequence[ChannelId], ctx: RequestContext) -> list[ChannelRecord]: ...

    async def channel_by_handle(self, handle: str, ctx: RequestContext) -> ChannelRecord | None: ...

    async def channel_by_username(self, username: str, ctx: RequestContext) -> ChannelRecord | None: ...

    async def uploads_page(
        self, playlist_id: str, page_token: str | None, ctx: RequestContext, etag: str | None = None
    ) -> UploadsPage | NotModified: ...

    async def videos_by_id(
        self, ids: Sequence[VideoId], ctx: RequestContext, with_localizations: bool = False
    ) -> VideoBatch: ...

    # stats-only refresh through videos:batchGetStats (own bucket, at most 50 ids per call)
    async def video_stats(self, ids: Sequence[VideoId], ctx: RequestContext) -> StatsBatch: ...

    async def search(self, query: SearchQuery, page_token: str | None, ctx: RequestContext) -> SearchPage: ...

    async def comment_threads(
        self, video_id: VideoId, max_results: int, ctx: RequestContext
    ) -> CommentPage: ...

    async def playlist_owner(self, playlist_id: str, ctx: RequestContext) -> ChannelId | None: ...


class AnalyticsQuery(DomainModel):
    channel_id: ChannelId
    start_date: date
    end_date: date
    metrics: list[str]
    dimensions: list[str] = []
    filters: str | None = None
    sort: str | None = None
    max_results: int | None = None


class AnalyticsTable(DomainModel):
    column_headers: list[str]
    rows: list[list[str | int | float | None]]


class YouTubeAnalyticsGateway(Protocol):
    # owner-authorised data only; never shared across clients
    async def report(self, access_token: SecretStr, query: AnalyticsQuery) -> AnalyticsTable: ...
