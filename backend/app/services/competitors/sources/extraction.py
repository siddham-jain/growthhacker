import re

from pydantic import BaseModel, ConfigDict, Field

from app.domain.competitors import CandidateSource
from app.ports.llm import PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.ports.search import WebSearchHit
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import registrable_domain
from app.services.competitors.model import RawCandidate
from app.services.competitors.providers import structured
from app.services.competitors.sources.base import SourceDeps

_SPACES = re.compile(r"\s+")
MAX_RESULTS_PER_CALL = 40


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = None
    quote: str = Field(min_length=1, max_length=500)
    result_id: int = Field(ge=0)


class ExtractedEntities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[ExtractedEntity] = Field(max_length=80)


def squash(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()


def quote_is_verbatim(quote: str, text: str) -> bool:
    return squash(quote) in squash(text)


def checked_domain(domain: str | None, hit: WebSearchHit) -> tuple[str | None, bool]:
    """the model's domain as a public registrable domain, and whether the result itself links or names it."""
    claimed = registrable_domain(domain)
    if claimed is None:
        return None, False
    shown = registrable_domain(hit.url) == claimed or claimed in squash(f"{hit.title} {hit.snippet}")
    return claimed, shown


class SearchEntityExtractor:
    """names organisations in search results; each must come with a verbatim quote from that result."""

    def __init__(self, deps: SourceDeps) -> None:
        self.llm = deps.llm

    async def extract(
        self, ctx: DiscoveryContext, hits: list[WebSearchHit], kind: str, cap: int
    ) -> list[RawCandidate]:
        hits = hits[:MAX_RESULTS_PER_CALL]
        if not hits:
            return []
        prompt = PromptInput(
            trusted=[
                TrustedBlock(
                    label="task",
                    text=(
                        f"Name the businesses that compete with {ctx.profile.identity.name} mentioned in the results. "
                        "Quote the exact sentence fragment that names each one."
                    ),
                )
            ],
            untrusted=[
                UntrustedBlock(label=f"result {index}", source=hit.url, text=f"{hit.title}\n{hit.snippet}")
                for index, hit in enumerate(hits)
            ],
        )
        output = await structured(
            ctx,
            self.llm,
            task=TaskId.competitor_entity_extraction,
            schema=ExtractedEntities,
            prompt_input=prompt,
            tier="low",
        )
        if output is None:
            return []
        candidates = []
        for entity in output.entities:
            if entity.result_id >= len(hits):
                continue
            hit = hits[entity.result_id]
            text = f"{hit.title} {hit.snippet}"
            if not quote_is_verbatim(entity.quote, text) or squash(entity.name) not in squash(text):
                continue
            # snippet text steers the model, so a domain the result never showed stays unverified like a guess
            domain, grounded = checked_domain(entity.domain, hit)
            candidates.append(
                RawCandidate(
                    name=entity.name,
                    source=CandidateSource(
                        kind=kind, ref=hit.url, snippet=entity.quote[:500], retrieved_at=ctx.now
                    ),
                    domain=domain,
                    domain_grounded=grounded,
                )
            )
        return candidates[:cap]
