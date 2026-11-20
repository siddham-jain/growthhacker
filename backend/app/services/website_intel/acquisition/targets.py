from collections.abc import Iterable

from app.domain.web import BlockReason, CoverageFacet
from app.services.website_intel.acquisition.models import CascadeRequest
from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.inputs.urls import in_scope
from app.services.website_intel.models import PageType

FACET_PAGE_TYPES: dict[CoverageFacet, tuple[PageType, ...]] = {
    CoverageFacet.offerings: (PageType.offering_hub, PageType.pricing, PageType.offering_detail),
    CoverageFacet.about: (PageType.about,),
    CoverageFacet.contact_locations: (PageType.contact, PageType.locations_hub),
    CoverageFacet.conversion_actions: (PageType.booking, PageType.contact, PageType.pricing),
}
RETRYABLE_BLOCKS = frozenset(
    {BlockReason.waf_block, BlockReason.cf_challenge, BlockReason.empty_shell, BlockReason.other}
)


def target_urls(
    request: CascadeRequest,
    classifier: PageTypeClassifier,
    *,
    limit: int,
    extra: Iterable[str] = (),
    include_blocked: bool = True,
) -> list[str]:
    """urls worth a later tier: the home page, earlier blocks, then candidates for missing facets."""
    if request.site is None or limit <= 0:
        return []
    home = request.site.canonical_origin + "/"
    fetched = set(request.fetched_urls)
    chosen: list[str] = []

    def add(url: str) -> None:
        if (
            url not in chosen
            and url not in fetched
            and in_scope(url, request.site.scope_hosts if request.site else [])
            and request.robots_allows(url)
        ):
            chosen.append(url)

    if not request.home_covered:
        add(home)
    if include_blocked:
        for attempt in request.prior_blocked:
            if attempt.reason in RETRYABLE_BLOCKS:
                add(attempt.url)
    wanted = [page_type for facet in request.missing_facets for page_type in FACET_PAGE_TYPES.get(facet, ())]
    pool = list(dict.fromkeys([*request.candidate_urls, *extra]))
    typed = [(url, classifier.classify(url)) for url in pool]
    for page_type in wanted:
        for url, guess in typed:
            if guess is page_type:
                add(url)
                break
    return chosen[:limit]
