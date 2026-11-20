from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import CrawlBudget, SourceKind
from app.domain.youtube import ChannelRecord, QuotaPriority
from app.ports.progress import ProgressReporter
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.identity.site_from_channel import urls_in
from app.services.website_intel.models import PageLink

CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"
MAX_UPLOADS = 20
MAX_UPLOAD_DESCRIPTION_CHARS = 400


def channel_text(channel: ChannelRecord, uploads: list[tuple[str, str]]) -> str:
    lines = [f"YouTube channel: {channel.title}"]
    if channel.description:
        lines.append(channel.description)
    if channel.keywords:
        lines.append("Channel keywords: " + ", ".join(channel.keywords))
    if uploads:
        lines.append("Recent video titles:")
        lines.extend(f"- {title}" for title, _ in uploads)
    return "\n".join(lines)


class YouTubeChannelSource:
    """tier 8: the channel snippet and recent uploads through 02's gateway; never youtube.com html."""

    source_kind: ClassVar[SourceKind] = SourceKind.youtube_channel
    tier: ClassVar[int] = 8

    def __init__(self, gateway: YouTubeGateway, toolkit: ContextToolkit) -> None:
        self._gateway = gateway
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return request.channel_id is not None or request.channel is not None

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        ctx = RequestContext(
            client_id=request.client_id, run_id=request.run_id, priority=QuotaPriority.interactive
        )
        channel = request.channel
        if channel is None and request.channel_id is not None:
            found = await self._gateway.channels_by_id([request.channel_id], ctx)
            channel = found[0] if found else None
        if channel is None:
            return harvest(self.source_kind, [], notes=["channel not found"])
        uploads: list[tuple[str, str]] = []
        if channel.recent_upload_ids:
            batch = await self._gateway.videos_by_id(channel.recent_upload_ids[:MAX_UPLOADS], ctx)
            uploads = [(video.title, video.description) for video in batch.found]
        descriptions = [channel.description, *(description for _, description in uploads)]
        links = [
            PageLink(url=url, anchor="")
            for text in descriptions
            for url in urls_in(text[: MAX_UPLOAD_DESCRIPTION_CHARS * 4])
        ]
        page = self._toolkit.extractor.from_text(
            text=channel_text(channel, uploads),
            url=CHANNEL_URL.format(channel_id=channel.channel_id),
            title=channel.title,
            source_kind=self.source_kind,
            fetched_at=channel.metadata_fetched_at,
            links=list({link.url: link for link in links}.values()),
            lang=channel.default_language,
            country_hint=request.country_hint or channel.country,
        )
        page = page.model_copy(update={"page": page.page.model_copy(update={"page_type": "other"})})
        return harvest(self.source_kind, [page], fetches=2 if uploads else 1)


@context_source_registry.register(SourceKind.youtube_channel.value)
def build_youtube_channel(toolkit: ContextToolkit) -> YouTubeChannelSource | None:
    return YouTubeChannelSource(toolkit.youtube, toolkit) if toolkit.youtube is not None else None
