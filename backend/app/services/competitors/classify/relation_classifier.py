import asyncio
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.competitors import CandidateStatus, CompetitorRelation, OfferingMatch, OwnerKind
from app.ports.llm import LLMClient, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.competitors.classify.geo import geo_ref
from app.services.competitors.classify.owner_kind import owner_kind_from_title
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity
from app.services.competitors.providers import structured
from app.services.competitors.sources.extraction import quote_is_verbatim

BATCH_SIZE = 15
MAX_BATCHES = 2
MAX_SNIPPETS = 3
SNIPPET_CHARS = 500
EXCLUDED_RELATIONS = frozenset(
    {
        CompetitorRelation.self_,
        CompetitorRelation.subsidiary,
        CompetitorRelation.parent,
        CompetitorRelation.partner,
        CompetitorRelation.supplier,
        CompetitorRelation.insurer,
        CompetitorRelation.unrelated,
    }
)
ORGANISATION_RELATIONS = frozenset(
    {
        CompetitorRelation.direct,
        CompetitorRelation.direct_public,
        CompetitorRelation.aggregator,
        CompetitorRelation.benchmark,
    }
)
GUESS_TO_RELATION = {
    "direct": CompetitorRelation.direct,
    "direct_public": CompetitorRelation.direct_public,
    "aggregator": CompetitorRelation.aggregator,
    "content": CompetitorRelation.content,
    "benchmark": CompetitorRelation.benchmark,
}


class ClassifiedGeo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = None
    region: str | None = None
    country_iso2: str
    quote: str


class ClassifiedOffering(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offering_id: str
    verdict: Literal["yes", "partial", "no"]
    quote: str = ""
    source_url: str = ""


class ClassifiedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    relation: CompetitorRelation
    owner_kind: OwnerKind = OwnerKind.unknown
    geography: list[ClassifiedGeo] = []
    offerings: list[ClassifiedOffering] = []
    same_name_other_country: bool = False


class ClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[ClassifiedEntity] = Field(max_length=BATCH_SIZE)


def evidence_texts(entity: CandidateEntity) -> list[tuple[str, str]]:
    texts = [(source.ref, source.snippet[:SNIPPET_CHARS]) for source in entity.sources if source.snippet]
    for record in entity.channel_hints.values():
        if record is not None:
            texts.append(
                (f"https://www.youtube.com/channel/{record.channel_id}", record.description[:SNIPPET_CHARS])
            )
    return texts[: MAX_SNIPPETS + 1]


class RelationClassifier:
    """one batched call decides relation, geography and offering overlap; every claim needs a quote."""

    def __init__(self, llm: LLMClient | None) -> None:
        self.llm = llm

    async def classify(self, entities: list[CandidateEntity], ctx: DiscoveryContext) -> None:
        pending = [
            entity for entity in entities if not entity.is_excluded and "carried_over" not in entity.flags
        ]
        batches = [pending[index : index + BATCH_SIZE] for index in range(0, len(pending), BATCH_SIZE)]
        classified: set[str] = set()
        results = await asyncio.gather(*(self._classify_batch(batch, ctx) for batch in batches[:MAX_BATCHES]))
        for batch, output in zip(batches, results, strict=False):
            by_key = {item.key: item for item in output.entities} if output else {}
            for entity in batch:
                item = by_key.get(entity.key)
                if item is not None:
                    self._apply(entity, item, ctx)
                    classified.add(entity.key)
        for entity in pending:
            if entity.key not in classified:
                self._fallback(entity)
            self._finish(entity)

    async def _classify_batch(
        self, batch: list[CandidateEntity], ctx: DiscoveryContext
    ) -> ClassificationBatch | None:
        profile = ctx.profile
        offerings = "\n".join(f"[{offering.id}] {offering.name}" for offering in profile.offerings[:30])
        client = (
            f"Client: {profile.identity.name}; model: {profile.identity.business_model.value}; "
            f"cities: {', '.join(profile.locations.cities[:15]) or 'unknown'}; "
            f"countries: {', '.join(profile.locations.countries or profile.identity.primary_markets) or 'unknown'}"
        )
        untrusted = []
        for entity in batch:
            body = "\n".join(f"({url}) {text}" for url, text in evidence_texts(entity))
            untrusted.append(
                UntrustedBlock(
                    label=f"entity {entity.key}",
                    source=entity.official_domain or "web",
                    text=f"name: {entity.brand_name}\ndomain: {entity.official_domain or 'unknown'}\n{body}",
                )
            )
        prompt = PromptInput(
            trusted=[
                TrustedBlock(label="client", text=client),
                TrustedBlock(label="offerings", text=offerings),
            ],
            untrusted=untrusted,
        )
        return await structured(
            ctx,
            self.llm,
            task=TaskId.competitor_relation_classification,
            schema=ClassificationBatch,
            prompt_input=prompt,
            tier="medium",
        )

    def _apply(self, entity: CandidateEntity, item: ClassifiedEntity, ctx: DiscoveryContext) -> None:
        texts = evidence_texts(entity)
        corpus = " ".join(text for _, text in texts)
        known_urls = {url for url, _ in texts}
        offering_ids = {offering.id for offering in ctx.profile.offerings}
        entity.relation = item.relation
        if item.owner_kind is not OwnerKind.unknown:
            entity.owner_kind = item.owner_kind
        for geo in item.geography:
            ref = geo_ref(geo.city, geo.region, geo.country_iso2)
            if (
                ref is not None
                and geo.quote
                and quote_is_verbatim(geo.quote, corpus)
                and ref not in entity.geography
            ):
                entity.geography.append(ref)
        matches = []
        for offering in item.offerings:
            if offering.verdict == "no" or offering.offering_id not in offering_ids:
                continue
            if not offering.quote or not quote_is_verbatim(offering.quote, corpus):
                continue
            source_url = (
                offering.source_url if offering.source_url in known_urls else (texts[0][0] if texts else "")
            )
            matches.append(
                OfferingMatch(
                    offering_id=offering.offering_id,
                    verdict=offering.verdict,
                    quote=offering.quote[:300],
                    source_url=source_url,
                )
            )
        entity.offering_matches = list({match.offering_id: match for match in matches}.values())
        if item.same_name_other_country:
            entity.flags.add("same_name_other_country")

    @staticmethod
    def _fallback(entity: CandidateEntity) -> None:
        # rules only: niche channels are content, otherwise the most common competitive guess, else direct
        entity.flags.add("relation_unclassified")
        if entity.source_kinds == {"niche_search"}:
            entity.relation = CompetitorRelation.content
            return
        guesses = Counter(
            GUESS_TO_RELATION[guess] for guess in entity.relation_guesses if guess in GUESS_TO_RELATION
        )
        entity.relation = guesses.most_common(1)[0][0] if guesses else CompetitorRelation.direct

    @staticmethod
    def _finish(entity: CandidateEntity) -> None:
        relation = entity.relation or CompetitorRelation.direct
        entity.relation = relation
        if relation in EXCLUDED_RELATIONS:
            entity.status = CandidateStatus.excluded
            entity.rejection_reason = relation.value
            return
        if relation is CompetitorRelation.direct_public:
            entity.flags.add("public_institution")
        if relation is CompetitorRelation.aggregator:
            entity.flags.add("aggregator")
        entity.owner_kind = resolve_owner_kind(entity, relation)


def resolve_owner_kind(entity: CandidateEntity, relation: CompetitorRelation) -> OwnerKind:
    # deterministic first, then the classifier's answer; businesses default to organisations
    titles = [record.title for record in entity.channel_hints.values() if record is not None]
    by_rule = owner_kind_from_title(titles[0] if titles else entity.brand_name)
    if relation in ORGANISATION_RELATIONS:
        return OwnerKind.individual if by_rule is OwnerKind.individual and titles else OwnerKind.organisation
    if by_rule is not OwnerKind.unknown:
        return by_rule
    return entity.owner_kind
