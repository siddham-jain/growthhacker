from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from app.domain.competitors import CandidateSource
from app.ports.llm import ClientContextBlock, PromptInput, TaskId, TrustedBlock
from app.services.competitors.classify.geo import geo_ref
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate, RelationGuess
from app.services.competitors.providers import structured
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.sources.base import SourceDeps
from app.services.competitors.sources.brief import profile_brief

PER_RELATION_CAP = {"direct": 15, "direct_public": 15, "aggregator": 5, "benchmark": 5, "content": 5}


class LlmGeo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = None
    region: str | None = None
    country_iso2: str


class LlmCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    official_domain_guess: str | None = None
    relation_guess: RelationGuess
    geography: list[LlmGeo] = []
    why: str = Field(max_length=500)
    aliases: list[str] = []
    native_script_name: str | None = None


class LlmCandidateList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[LlmCandidate] = Field(max_length=40)


@candidate_generator_registry.register("llm")
class LlmCandidateSource:
    kind = "llm"
    used_in_rediscovery = True

    def __init__(self, deps: SourceDeps) -> None:
        self.llm = deps.llm

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return self.llm is not None and ctx.source_enabled(self.kind)

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        prompt = PromptInput(
            client_context=ClientContextBlock(text=profile_brief(ctx.profile, ctx.buyer_questions)),
            trusted=[
                TrustedBlock(
                    label="task",
                    text="List currently operating competitors of this business. Use 'unknown' when unsure of a domain.",
                )
            ],
        )
        output = await structured(
            ctx,
            self.llm,
            task=TaskId.competitor_candidate_generation,
            schema=LlmCandidateList,
            prompt_input=prompt,
            tier="medium",
        )
        if output is None:
            return []
        per_relation: Counter[str] = Counter()
        candidates = []
        for item in output.candidates:
            per_relation[item.relation_guess] += 1
            if per_relation[item.relation_guess] > PER_RELATION_CAP.get(item.relation_guess, 10):
                continue
            aliases = [*item.aliases, *([item.native_script_name] if item.native_script_name else [])]
            domain = (
                item.official_domain_guess
                if item.official_domain_guess not in {None, "", "unknown"}
                else None
            )
            candidates.append(
                RawCandidate(
                    name=item.name,
                    source=CandidateSource(
                        kind="llm", ref="profile", snippet=item.why[:500], retrieved_at=ctx.now
                    ),
                    domain=domain,
                    relation_guess=item.relation_guess,
                    aliases=aliases,
                    geography=[
                        geo
                        for raw in item.geography
                        if (geo := geo_ref(raw.city, raw.region, raw.country_iso2))
                    ],
                )
            )
        return candidates[: ctx.settings.source_caps.llm]
