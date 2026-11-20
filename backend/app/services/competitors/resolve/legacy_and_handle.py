from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import compact, domain_label
from app.services.competitors.model import CandidateEntity, LeadRef
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ResolverCost, ResolverDeps, has_likely_lead


def handle_guesses(entity: CandidateEntity, limit: int) -> list[str]:
    guesses = []
    if entity.official_domain:
        guesses.append(domain_label(entity.official_domain))
    name = compact(entity.brand_name)
    guesses.extend([name, f"{name}official"])
    known = {
        lead.record.handle.lstrip("@").casefold()
        for lead in entity.leads.values()
        if lead.record is not None and lead.record.handle
    }
    unique = [guess for guess in dict.fromkeys(guesses) if 3 <= len(guess) <= 30 and guess not in known]
    return unique[:limit]


@channel_resolver_registry.register("handle_guess")
class UsernameHandleResolver:
    """guessed handles earn no ownership credit for being found; they must earn it from signals."""

    name = "handle_guess"
    order = 40
    cost = ResolverCost(yt_units=3)

    def __init__(self, deps: ResolverDeps) -> None:
        self.youtube = deps.youtube

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        return not entity.channel_hints and not has_likely_lead(entity, ctx)

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        for guess in handle_guesses(entity, ctx.settings.max_handle_guesses):
            record = await self.youtube.by_handle(guess)
            if record is not None:
                return [
                    LeadRef(kind="channel_id", value=record.channel_id, method="for_handle", guessed=True)
                ]
        return []
