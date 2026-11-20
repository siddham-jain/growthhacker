from dataclasses import dataclass, field

from app.domain.common import ChannelId
from app.domain.youtube import ChannelRecord, ChannelRef, ChannelResolution, SearchQuery
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.youtube import messages
from app.services.youtube.input_parser import InputParser
from app.services.youtube.ownership import BusinessHint, OwnershipVerifier


@dataclass
class SearchAllowance:
    """search.list is the last resort for channel resolution, at most `limit` calls per run."""

    limit: int = 3
    used: int = field(default=0)

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


class ChannelResolver:
    def __init__(
        self,
        gateway: YouTubeGateway,
        verifier: OwnershipVerifier | None = None,
        parser: InputParser | None = None,
    ) -> None:
        self._gateway = gateway
        self._verifier = verifier
        self._parser = parser or InputParser()

    async def resolve(
        self,
        raw: str,
        ctx: RequestContext,
        *,
        hint: BusinessHint | None = None,
        search: SearchAllowance | None = None,
    ) -> ChannelResolution:
        ref = self._parser.parse(raw)
        channel, method, candidates, message = await self._lookup(ref, ctx, search)
        if channel is None:
            return ChannelResolution(
                input=ref,
                channel=None,
                method="none",
                candidates=candidates,
                needs_user_confirmation=True,
                message=message,
            )
        needs_confirmation = method == "custom_url_search"
        ownership = None
        if hint is not None and self._verifier is not None:
            # a name the api resolved to this channel is the channel, even when it is not its handle
            aliases = [f"{ref.kind}:{ref.value}"] if method in ("username", "handle") else []
            ownership = await self._verifier.assess(channel, hint, aliases=aliases)
            needs_confirmation = needs_confirmation or ownership.label.value != "verified"
            if ownership.label.value == "rejected":
                message = messages.ownership_mismatch(
                    channel.handle, channel.title, channel.subscriber_count, hint.website_domain
                )
        return ChannelResolution(
            input=ref,
            channel=channel,
            method=method,
            candidates=candidates,
            ownership=ownership,
            needs_user_confirmation=needs_confirmation,
            message=message,
        )

    async def _by_id(self, channel_id: ChannelId | None, ctx: RequestContext) -> ChannelRecord | None:
        if not channel_id:
            return None
        found = await self._gateway.channels_by_id([channel_id], ctx)
        return found[0] if found else None

    async def _lookup(self, ref: ChannelRef, ctx: RequestContext, search: SearchAllowance | None):
        if ref.kind == "channel_id":
            channel = await self._by_id(ref.value, ctx)
            return channel, "id", [], None if channel else messages.channel_gone(ref.value)
        if ref.kind == "video":
            batch = await self._gateway.videos_by_id([ref.value], ctx)
            if not batch.found:
                return None, "none", [], messages.UNKNOWN_INPUT
            channel = await self._by_id(batch.found[0].channel_id, ctx)
            return channel, "video", [], messages.from_video(channel.title) if channel else None
        if ref.kind == "playlist":
            channel = await self._by_id(await self._gateway.playlist_owner(ref.value, ctx), ctx)
            return channel, "playlist", [], None if channel else messages.UNKNOWN_INPUT
        if ref.kind in ("handle", "username", "custom_url"):
            return await self._by_name(ref, ctx, search)
        return None, "none", [], messages.UNKNOWN_INPUT

    async def _by_name(self, ref: ChannelRef, ctx: RequestContext, search: SearchAllowance | None):
        order = ("username", "handle") if ref.kind == "username" else ("handle", "username")
        for kind in order:
            lookup = (
                self._gateway.channel_by_handle if kind == "handle" else self._gateway.channel_by_username
            )
            channel = await lookup(ref.value, ctx)
            if channel is not None:
                return channel, kind, [], None
        fallback = messages.CUSTOM_URL if ref.kind == "custom_url" else messages.handle_not_found(ref.value)
        if search is None or not search.take():
            return None, "none", [], fallback
        page = await self._gateway.search(SearchQuery(q=ref.value, type="channel"), None, ctx)
        candidates = await self._gateway.channels_by_id([hit.id for hit in page.items[:10]], ctx)
        wanted = f"@{ref.value}".casefold()
        exact = [candidate for candidate in candidates if (candidate.handle or "").casefold() == wanted]
        if len(exact) == 1:
            return exact[0], "custom_url_search", candidates, None
        return None, "none", candidates, fallback
