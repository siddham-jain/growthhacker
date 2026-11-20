from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.identity.knowledge import KbEntity, KbSummary, KnowledgeBase, pick_entity

WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"


def knowledge_text(entity: KbEntity, summary: KbSummary | None) -> str:
    # one fact per line so verbatim quote checks can bind to a single statement
    lines = [f"{entity.label}: {entity.description}" if entity.description else entity.label]
    if summary is not None:
        lines.append(summary.extract)
    for label, values in (
        ("Official website", entity.websites),
        ("Industry", entity.industries),
        ("Headquarters", entity.headquarters),
        ("Country", entity.countries),
        ("Products", entity.products),
        ("Also known as", entity.aliases),
    ):
        if values:
            lines.append(f"{label}: {', '.join(values)}")
    for label, related in (("Subsidiaries", entity.subsidiaries), ("Parent organisation", entity.parents)):
        if related:
            lines.append(f"{label}: " + ", ".join(item.name for item in related))
    if entity.inception_year:
        lines.append(f"Founded: {entity.inception_year}")
    return "\n".join(lines)


class KnowledgeBaseSource:
    """tier 6: wikipedia summary + wikidata facts; cheap, always run, labelled as needing confirmation."""

    source_kind: ClassVar[SourceKind] = SourceKind.knowledge_base
    tier: ClassVar[int] = 6

    def __init__(self, knowledge: KnowledgeBase, toolkit: ContextToolkit) -> None:
        self._knowledge = knowledge
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return bool(request.business_name or request.knowledge_entity)

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        entity = request.knowledge_entity
        if entity is None and request.business_name:
            candidates = await self._knowledge.search(request.business_name)
            domain = request.site.registrable_domain if request.site else None
            entity = pick_entity(candidates, name=request.business_name, domain=domain)
        if entity is None:
            return harvest(self.source_kind, [], notes=["no knowledge-base entry matched"])
        summary = (
            await self._knowledge.summary(entity.wikipedia_title, language=entity.wikipedia_lang)
            if entity.wikipedia_title
            else None
        )
        page = self._toolkit.extractor.from_text(
            text=knowledge_text(entity, summary),
            url=summary.url if summary else WIKIDATA_ENTITY_URL.format(qid=entity.qid),
            title=entity.label,
            source_kind=self.source_kind,
            fetched_at=self._toolkit.clock.now(),
            lang=entity.wikipedia_lang,
        )
        page = page.model_copy(update={"page": page.page.model_copy(update={"page_type": "about"})})
        return harvest(self.source_kind, [page], knowledge_entity=entity, fetches=2)


@context_source_registry.register(SourceKind.knowledge_base.value)
def build_knowledge_base(toolkit: ContextToolkit) -> KnowledgeBaseSource | None:
    return KnowledgeBaseSource(toolkit.knowledge, toolkit) if toolkit.knowledge is not None else None
