from collections.abc import Callable, Iterable, Sequence
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from app.api.errors import NotFound
from app.api.pagination import Cursors, Page, PageRequest
from app.api.schemas.clients import ChannelRef
from app.api.schemas.results import VideoSummary
from app.api.signing import Signer
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.models.tenancy import Client
from app.models.youtube_cache import YtChannel, YtVideo
from app.pipeline.artifacts import ArtifactExpiry, ArtifactMissing, ArtifactSchemaMismatch, PgArtifactStore
from app.ports.artifacts import ArtifactKey


class ArtifactNotStored(NotFound):
    code = "results_not_ready"
    title = "This part of the analysis is not available for the published run"


class ServiceBase:
    """what every read service shares: the container, opaque cursors and the published run's artifacts."""

    def __init__(self, container: Container) -> None:
        self.container = container
        self.settings = container.settings
        self.clock = container.clock
        self.session_factory = container.session_factory
        self.cursors = Cursors(Signer.from_settings(container.settings))

    def artifacts(self, scope: TenantScope) -> PgArtifactStore:
        return PgArtifactStore(
            self.session_factory,
            scope,
            self.clock,
            ArtifactExpiry(
                api_data_days=self.settings.policy.retention_days,
                other_days=self.settings.retention.run_artifacts_ttl_days,
            ),
        )

    async def artifact[T: BaseModel](self, scope: TenantScope, run_id: UUID, key: ArtifactKey[T]) -> T:
        try:
            return await self.artifacts(scope).get(run_id, key)
        except (ArtifactMissing, ArtifactSchemaMismatch):
            raise ArtifactNotStored(f"the published run has no '{key.name}' results") from None

    async def optional_artifact[T: BaseModel](
        self, scope: TenantScope, run_id: UUID, key: ArtifactKey[T]
    ) -> T | None:
        try:
            return await self.artifacts(scope).get(run_id, key)
        except (ArtifactMissing, ArtifactSchemaMismatch):
            return None

    async def client(self, scope: TenantScope) -> Client:
        async with self.session_factory() as session:
            client = await session.get(Client, scope.client_id)
        if client is None or client.workspace_id != scope.workspace_id:
            raise NotFound("client not found")
        return client

    def paginate[T](self, items: Sequence[T], page: PageRequest, filters: dict[str, Any]) -> Page[T]:
        # offset cursors over an already ordered, immutable result set (a published run)
        after = self.cursors.decode(page.cursor, filters)
        start = int(after[0]) if after else 0
        chunk = list(items[start : start + page.limit])
        end = start + len(chunk)
        next_cursor = self.cursors.encode([end], filters) if end < len(items) else None
        return Page(items=chunk, next_cursor=next_cursor, limit=page.limit, total=len(items))


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


async def video_summaries(session_factory: Any, video_ids: Iterable[str]) -> dict[str, VideoSummary]:
    wanted = sorted(set(video_ids))
    if not wanted:
        return {}
    async with session_factory() as session:
        rows = (await session.execute(select(YtVideo).where(YtVideo.video_id.in_(wanted)))).scalars().all()
    found = {
        row.video_id: VideoSummary(
            video_id=row.video_id,
            channel_id=row.channel_id,
            title=row.title,
            channel_title=row.channel_title,
            published_at=row.published_at,
            url=video_url(row.video_id),
            thumbnail_url=thumbnail_url(row.video_id),
        )
        for row in rows
    }
    return found


def summary_or_bare(summaries: dict[str, VideoSummary], video_id: str, channel_id: str) -> VideoSummary:
    # a video purged from the cache under the 28-day rule still renders with its id only
    return summaries.get(video_id) or VideoSummary(
        video_id=video_id,
        channel_id=channel_id,
        title=None,
        channel_title=None,
        published_at=None,
        url=video_url(video_id),
        thumbnail_url=thumbnail_url(video_id),
    )


async def channel_refs(
    session_factory: Any, channel_ids: Iterable[str], verified: Callable[[str], bool] = lambda _: False
) -> dict[str, ChannelRef]:
    wanted = sorted(set(channel_ids))
    rows: dict[str, YtChannel] = {}
    if wanted:
        async with session_factory() as session:
            result = await session.execute(select(YtChannel).where(YtChannel.channel_id.in_(wanted)))
            rows = {row.channel_id: row for row in result.scalars()}
    return {
        channel_id: channel_ref(channel_id, rows.get(channel_id), verified(channel_id))
        for channel_id in wanted
    }


def channel_ref(channel_id: str, row: YtChannel | None, verified: bool = False) -> ChannelRef:
    return ChannelRef(
        channel_id=channel_id,
        title=row.title if row else None,
        handle=row.handle if row else None,
        thumbnail_url=row.thumbnail_url if row else None,
        subscriber_count=None if row is None or row.hidden_subscriber_count else row.subscriber_count,
        verified=verified,
    )


def sort_items[T](items: list[T], sort: str, keys: dict[str, Callable[[T], Any]]) -> list[T]:
    """one transparent, whitelisted criterion; `-field` sorts descending, missing values go last."""
    descending = sort.startswith("-")
    name = sort.removeprefix("-")
    key = keys[name]
    present = [item for item in items if key(item) is not None]
    missing = [item for item in items if key(item) is None]
    return sorted(present, key=key, reverse=descending) + missing
