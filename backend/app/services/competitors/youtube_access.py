from collections.abc import Iterable, Sequence

from app.domain.youtube import ChannelRecord, SearchPage, SearchQuery, VideoRecord
from app.ports.quota import QuotaExhausted
from app.ports.youtube import NotModified, UploadsPage, YouTubeGateway
from app.services.competitors.context import DiscoveryContext, SearchPurpose

BATCH = 50


def chunks[T](items: Sequence[T], size: int = BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MeteredYouTube:
    """the gateway behind a per-run meter; 02's gateway still meters the project quota itself."""

    def __init__(self, gateway: YouTubeGateway, ctx: DiscoveryContext) -> None:
        self.gateway = gateway
        self.ctx = ctx
        self.meter = ctx.meter
        self._channels: dict[str, ChannelRecord | None] = {}
        self._videos: dict[str, VideoRecord | None] = {}

    async def channels(self, ids: Iterable[str]) -> dict[str, ChannelRecord]:
        wanted = [channel_id for channel_id in dict.fromkeys(ids) if channel_id not in self._channels]
        for batch in chunks(wanted):
            self.meter.reserve_youtube(1)
            records = await self.gateway.channels_by_id(list(batch), self.ctx.request_context)
            found = {record.channel_id: record for record in records}
            for channel_id in batch:
                self._channels[channel_id] = found.get(channel_id)
        return {
            channel_id: record for channel_id in ids if (record := self._channels.get(channel_id)) is not None
        }

    def remember(self, record: ChannelRecord) -> None:
        self._channels[record.channel_id] = record

    async def by_handle(self, handle: str) -> ChannelRecord | None:
        self.meter.reserve_youtube(1)
        record = await self.gateway.channel_by_handle(handle, self.ctx.request_context)
        if record is not None:
            self.remember(record)
        return record

    async def by_username(self, username: str) -> ChannelRecord | None:
        self.meter.reserve_youtube(1)
        record = await self.gateway.channel_by_username(username, self.ctx.request_context)
        if record is not None:
            self.remember(record)
        return record

    async def videos(self, ids: Iterable[str]) -> dict[str, VideoRecord]:
        wanted = [video_id for video_id in dict.fromkeys(ids) if video_id not in self._videos]
        for batch in chunks(wanted):
            self.meter.reserve_youtube(1)
            result = await self.gateway.videos_by_id(list(batch), self.ctx.request_context)
            found = {video.video_id: video for video in result.found}
            for video_id in batch:
                self._videos[video_id] = found.get(video_id)
        return {video_id: video for video_id in ids if (video := self._videos.get(video_id)) is not None}

    async def uploads(self, playlist_id: str) -> UploadsPage | None:
        self.meter.reserve_youtube(1)
        page = await self.gateway.uploads_page(playlist_id, None, self.ctx.request_context)
        return None if isinstance(page, NotModified) else page

    async def playlist_owner(self, playlist_id: str) -> str | None:
        if playlist_id.startswith("UU"):
            return f"UC{playlist_id[2:]}"
        self.meter.reserve_youtube(1)
        return await self.gateway.playlist_owner(playlist_id, self.ctx.request_context)

    async def search(self, query: SearchQuery, purpose: SearchPurpose) -> SearchPage:
        self.meter.reserve_search(purpose)
        try:
            page = await self.gateway.search(query, None, self.ctx.request_context)
        except QuotaExhausted:
            # the quota manager refused before anything reached google
            self.meter.settle_search(purpose, spent=False)
            raise
        self.meter.settle_search(purpose, spent=not page.from_cache)
        return page
