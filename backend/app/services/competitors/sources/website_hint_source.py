from app.domain.competitors import CandidateSource
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.sources.base import SourceDeps


@candidate_generator_registry.register("website_hint")
class WebsiteHintCandidateSource:
    kind = "website_hint"
    used_in_rediscovery = False

    def __init__(self, deps: SourceDeps) -> None:
        pass

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return bool(ctx.profile.competitor_hints) and ctx.source_enabled(self.kind)

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        candidates = []
        for hint in ctx.profile.competitor_hints[: ctx.settings.source_caps.website_hint]:
            evidence = hint.meta.evidence[0] if hint.meta.evidence else None
            candidates.append(
                RawCandidate(
                    name=hint.name,
                    domain=hint.domain,
                    source=CandidateSource(
                        kind=self.kind,
                        ref=hint.url or (evidence.source_url if evidence else "profile"),
                        snippet=(evidence.quote[:500] if evidence else None),
                        retrieved_at=ctx.now,
                    ),
                )
            )
        return candidates
