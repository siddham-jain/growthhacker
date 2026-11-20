from app.api.errors import Unavailable
from app.api.schemas.clients import ChannelRef
from app.api.schemas.setup import ChannelCandidate, ChannelResolveOut
from app.api.services import CHANNELS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.container import Container
from app.core.registry import UnknownRegistrationError
from app.core.tenancy import TenantScope
from app.domain.youtube import ChannelRecord, OwnershipLabel, OwnershipVerdict, QuotaPriority
from app.models.tenancy import Client
from app.ports.youtube import RequestContext
from app.services.youtube.ownership import BusinessHint
from app.services.youtube.resolve import SearchAllowance

CONFIDENCE = {OwnershipLabel.verified: "high", OwnershipLabel.likely: "medium"}
# a custom url can only be found through search.list, the binding quota; one call at most
SEARCH_CALLS = 1


def candidate(
    channel: ChannelRecord, ownership: OwnershipVerdict | None, notes: list[str]
) -> ChannelCandidate:
    verified = ownership is not None and ownership.label is OwnershipLabel.verified
    evidence = [signal.detail for signal in ownership.signals] if ownership else []
    return ChannelCandidate(
        channel=ChannelRef(
            channel_id=channel.channel_id,
            title=channel.title,
            handle=channel.handle,
            thumbnail_url=channel.thumbnail_url,
            subscriber_count=None if channel.hidden_subscriber_count else channel.subscriber_count,
            verified=verified,
            verified_by="website_link" if verified and ownership and ownership.method == "link" else None,
        ),
        ownership_evidence=[*evidence, *notes],
        confidence=CONFIDENCE.get(ownership.label, "low") if ownership else "low",  # type: ignore[arg-type]
    )


def onboarding_verdict(client: Client, channel_id: str) -> OwnershipVerdict | None:
    """the verdict onboarding stored for its proposed channel; it saw the site's links, this lookup does not."""
    if client.own_channel_id != channel_id:
        return None
    for entry in client.own_channel_verification or []:
        if isinstance(entry, dict) and entry.get("label") and "signals" in entry:
            return OwnershipVerdict(
                label=entry["label"],
                method="link" if entry.get("verified_by") == "website_link" else None,
                score=entry.get("score") or 0.0,
                signals=entry["signals"],
            )
    return None


class ChannelResolveService(ServiceBase):
    """02's resolver with the client's site as the ownership hint; it never attaches a channel itself."""

    async def resolve(self, scope: TenantScope, channel_ref: str) -> ChannelResolveOut:
        try:
            layer = self.container.adapter("youtube_data_layer")
        except (LookupError, UnknownRegistrationError):
            raise Unavailable("YouTube lookups are not configured on this deployment") from None
        client = await self.client(scope)
        resolution = await layer.resolver.resolve(
            channel_ref,
            RequestContext(client_id=client.id, run_id=None, priority=QuotaPriority.interactive),
            hint=BusinessHint(name=client.name, website_domain=client.website_domain, country=client.country),
            search=SearchAllowance(limit=SEARCH_CALLS),
        )
        candidates = []
        if resolution.channel is not None:
            stored = onboarding_verdict(client, resolution.channel.channel_id)
            notes = [resolution.message] if resolution.message and stored is None else []
            candidates.append(candidate(resolution.channel, stored or resolution.ownership, notes))
        seen = {item.channel.channel_id for item in candidates}
        for channel in resolution.candidates:
            if channel.channel_id not in seen:
                seen.add(channel.channel_id)
                candidates.append(candidate(channel, None, []))
        return ChannelResolveOut(candidates=candidates)


@api_service_registry.register(CHANNELS)
def build_channel_service(container: Container) -> ChannelResolveService:
    return ChannelResolveService(container)
