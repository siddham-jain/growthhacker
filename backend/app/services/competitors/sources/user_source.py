from app.domain.competitors import CandidateSource
from app.domain.youtube import ChannelRecord
from app.pipeline.contracts import BudgetExceeded
from app.ports.quota import QuotaExhausted
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import domain_label
from app.services.competitors.model import (
    LeadRef,
    LinkEvidence,
    RawCandidate,
    ResolutionMethod,
    UserCompetitorInput,
)
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.resolve.youtube_url import (
    NameRef,
    RejectedRef,
    WebsiteRef,
    YouTubeRef,
    parse_youtube_url,
)
from app.services.competitors.sources.base import SourceDeps
from app.services.competitors.youtube_access import MeteredYouTube

METHOD_BY_KIND: dict[str, ResolutionMethod] = {
    "channel_id": "user_url",
    "handle": "for_handle",
    "username": "for_username",
    "custom": "for_username",
    "video": "video_owner",
    "playlist": "playlist_owner",
}


async def resolve_youtube_ref(
    youtube: MeteredYouTube, ref: YouTubeRef
) -> tuple[ChannelRecord | None, str | None]:
    """returns the owning channel and, for video or playlist links, a note naming the owner."""
    if ref.kind == "channel_id":
        return (await youtube.channels([ref.value])).get(ref.value), None
    if ref.kind == "handle":
        return await youtube.by_handle(ref.value), None
    if ref.kind in {"username", "custom"}:
        return await youtube.by_username(ref.value) or await youtube.by_handle(ref.value), None
    if ref.kind == "video":
        video = (await youtube.videos([ref.value])).get(ref.value)
        if video is None:
            return None, None
        record = (await youtube.channels([video.channel_id])).get(video.channel_id)
        return record, f"That video was published by {video.channel_title}."
    owner = await youtube.playlist_owner(ref.value)
    if owner is None:
        return None, None
    record = (await youtube.channels([owner])).get(owner)
    return record, f"This playlist belongs to {record.title}." if record else None


@candidate_generator_registry.register("user")
class UserCandidateSource:
    kind = "user"
    used_in_rediscovery = False

    def __init__(self, deps: SourceDeps) -> None:
        self.youtube = deps.youtube

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return bool(ctx.user_inputs) and ctx.source_enabled(self.kind)

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        candidates = []
        for user_input in ctx.user_inputs[: ctx.settings.source_caps.user]:
            candidate = await self.parse(ctx, user_input)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def parse(self, ctx: DiscoveryContext, user_input: UserCompetitorInput) -> RawCandidate | None:
        parsed = parse_youtube_url(user_input.raw)
        source = CandidateSource(kind=self.kind, ref=user_input.raw[:400], retrieved_at=ctx.now)
        match parsed:
            case RejectedRef(reason=reason):
                message = (
                    "Only YouTube channels can be tracked."
                    if "other platforms" in reason
                    else f"We couldn't use '{user_input.raw[:60]}': {reason}."
                )
                ctx.warn("USER_INPUT_REJECTED", message)
                return None
            case NameRef(name=name):
                return RawCandidate(name=name, source=source, intended_role=user_input.intended_role)
            case WebsiteRef(domain=domain):
                return RawCandidate(
                    name=domain_label(domain),
                    domain=domain,
                    source=source,
                    intended_role=user_input.intended_role,
                )
            case YouTubeRef() as ref:
                return await self._channel_candidate(ctx, user_input, ref, source)
        return None

    async def _channel_candidate(
        self, ctx: DiscoveryContext, user_input: UserCompetitorInput, ref: YouTubeRef, source: CandidateSource
    ) -> RawCandidate | None:
        try:
            record, note = await resolve_youtube_ref(self.youtube, ref)
        except (BudgetExceeded, QuotaExhausted):
            ctx.warn("USER_INPUT_DEFERRED", "We'll check the channel you added once YouTube quota allows.")
            return None
        if record is None:
            message = (
                f"No channel with handle @{ref.value}."
                if ref.kind == "handle"
                else "Couldn't find that channel. Paste a link from the channel's page (youtube.com/@…)."
            )
            ctx.warn("USER_CHANNEL_NOT_FOUND", message)
            return None
        if note:
            ctx.warn("USER_LINK_OWNER", note)
        lead = LeadRef(
            kind="channel_id",
            value=record.channel_id,
            method=METHOD_BY_KIND[ref.kind],
            evidence=[LinkEvidence(name="user_asserted", detail=f"You added {user_input.raw[:120]}")],
        )
        return RawCandidate(
            name=record.title,
            channel_id=record.channel_id,
            channel_record=record,
            source=source,
            lead_refs=[lead],
            intended_role=user_input.intended_role,
        )
