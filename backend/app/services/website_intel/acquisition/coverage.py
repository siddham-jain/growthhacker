from collections.abc import Iterable

from app.domain.web import CoverageFacet, SourceKind
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import ExtractedPage, PageType

REQUIRED_FACETS: frozenset[CoverageFacet] = frozenset(CoverageFacet)
OFFERING_TYPES = frozenset({PageType.offering_hub, PageType.offering_detail, PageType.pricing})
CONTACT_TYPES = frozenset({PageType.contact, PageType.locations_hub, PageType.location_detail})
CONTACT_JSON_LD = ("PostalAddress", "address", "telephone")
CONTACT_CTAS = frozenset({"call", "visit_location", "email", "whatsapp"})
ABOUT_MIN_CHARS = 1_500
CHANNEL_ABOUT_MIN_CHARS = 200
OFFERING_NAV_ITEMS = 9
SITE_SOURCES = frozenset(
    {
        SourceKind.site_direct,
        SourceKind.site_rendered,
        SourceKind.reader_proxy,
        SourceKind.search_index,
        SourceKind.archive,
    }
)


def _page_type(page: ExtractedPage) -> PageType:
    try:
        return PageType(page.page.page_type or "other")
    except ValueError:
        return PageType.other


class CoverageScorer:
    """which required facets a set of pages covers; the cascade stops once all are covered."""

    def facets(self, pages: Iterable[ExtractedPage]) -> frozenset[CoverageFacet]:
        covered: set[CoverageFacet] = set()
        for page in pages:
            covered |= self.page_facets(page)
        return frozenset(covered)

    def page_facets(self, page: ExtractedPage) -> set[CoverageFacet]:
        found: set[CoverageFacet] = set()
        page_type = _page_type(page)
        source = page.page.source_kind
        on_site = source in SITE_SOURCES
        if on_site and (page_type is PageType.home or path_of(page.page.url) == "/"):
            found.add(CoverageFacet.home)
        if page_type in OFFERING_TYPES or (
            on_site and sum(1 for item in page.details.nav if item.depth <= 2) >= OFFERING_NAV_ITEMS
        ):
            found.add(CoverageFacet.offerings)
        if (
            page_type is PageType.about
            or source is SourceKind.knowledge_base
            or (page_type is PageType.home and len(page.page.text) >= ABOUT_MIN_CHARS)
            or (source is SourceKind.youtube_channel and len(page.page.text) >= CHANNEL_ABOUT_MIN_CHARS)
        ):
            found.add(CoverageFacet.about)
        json_ld_text = str(page.page.json_ld)
        if (
            page_type in CONTACT_TYPES
            or any(marker in json_ld_text for marker in CONTACT_JSON_LD)
            or any(cta.type in CONTACT_CTAS for cta in page.details.ctas)
        ):
            found.add(CoverageFacet.contact_locations)
        if page.details.ctas:
            found.add(CoverageFacet.conversion_actions)
        return found
