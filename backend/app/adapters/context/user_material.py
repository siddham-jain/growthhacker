from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.models import ExtractedPage

USER_URL = "user://material/{index}"


class UserMaterialSource:
    """tier 9: text, brochures and url lists the user gave us; facts start user-confirmed."""

    source_kind: ClassVar[SourceKind] = SourceKind.user
    tier: ClassVar[int] = 9

    def __init__(self, toolkit: ContextToolkit) -> None:
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return bool(request.user_material)

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        pages: list[ExtractedPage] = []
        candidates: list[str] = []
        now = self._toolkit.clock.now()
        for index, material in enumerate(request.user_material):
            if material.kind == "url_list":
                candidates.extend(line.strip() for line in material.content.splitlines() if line.strip())
                continue
            url = material.source_url or USER_URL.format(index=index)
            if material.kind == "markdown":
                page = self._toolkit.extractor.from_markdown(
                    markdown=material.content,
                    requested_url=url,
                    final_url=url,
                    title=material.title,
                    site=request.site,
                    source_kind=self.source_kind,
                    fetched_at=now,
                    country_hint=request.country_hint,
                )
            else:
                page = self._toolkit.extractor.from_text(
                    text=material.content,
                    url=url,
                    title=material.title,
                    source_kind=self.source_kind,
                    fetched_at=now,
                    country_hint=request.country_hint,
                )
            pages.append(
                page.model_copy(update={"page": page.page.model_copy(update={"page_type": "about"})})
            )
        return harvest(self.source_kind, pages, remaining_urls=candidates)


@context_source_registry.register(SourceKind.user.value)
def build_user_material(toolkit: ContextToolkit) -> UserMaterialSource:
    return UserMaterialSource(toolkit)
