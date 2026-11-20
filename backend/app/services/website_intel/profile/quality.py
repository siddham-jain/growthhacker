from collections import Counter
from collections.abc import Sequence

from app.domain.profile import CoverageReport, ProfileQuality
from app.domain.web import SourceKind
from app.ports.context import BlockedAttempt
from app.services.website_intel.models import ExtractedPage, PageType

CHECKLIST: dict[str, tuple[PageType, ...]] = {
    "pricing": (PageType.pricing,),
    "faq": (PageType.faq,),
    "about": (PageType.about,),
    "contact": (PageType.contact,),
    "locations": (PageType.locations_hub, PageType.location_detail),
    "case_studies": (PageType.case_study,),
}
THIN_PAGES = 5
THIN_FACTS = 25
RICH_PAGES = 20
RICH_OFFERINGS = 5
RICH_VALUE_PROPS = 3


def coverage_report(
    *,
    pages: Sequence[ExtractedPage],
    used: int,
    discovered: int,
    blocked: Sequence[BlockedAttempt],
    robots_disallowed: int,
    has_prices: bool,
    has_faq_facts: bool,
    warnings: Sequence[str],
) -> CoverageReport:
    types = Counter(page.page.page_type or "other" for page in pages)
    missing = [
        name for name, wanted in CHECKLIST.items() if not any(types.get(kind.value) for kind in wanted)
    ]
    if has_prices and "pricing" in missing:
        missing.remove("pricing")
    if has_faq_facts and "faq" in missing:
        missing.remove("faq")
    reasons = Counter(attempt.reason.value for attempt in blocked)
    if robots_disallowed:
        reasons["robots"] = robots_disallowed
    return CoverageReport(
        pages_discovered=max(discovered, len(pages)),
        pages_fetched=len(pages),
        pages_used=used,
        page_types_found=dict(types),
        missing=missing,
        blocked=dict(reasons),
        sources_used=dict(Counter(SourceKind(page.page.source_kind) for page in pages)),
        languages=dict(Counter(page.page.lang for page in pages if page.page.lang)),
        warnings=list(dict.fromkeys(warnings)),
    )


def profile_quality(
    *,
    pages_used: int,
    verified_facts: int,
    dropped_facts: int,
    injection_flags: int,
    offerings: int,
    conversions: int,
    value_props: int,
    facets_covered: int,
    facets_total: int,
) -> ProfileQuality:
    thin = pages_used < THIN_PAGES or verified_facts < THIN_FACTS or offerings < 1
    if thin:
        label = "thin"
    elif (
        pages_used >= RICH_PAGES
        and offerings >= RICH_OFFERINGS
        and conversions >= 1
        and value_props >= RICH_VALUE_PROPS
    ):
        label = "rich"
    else:
        label = "adequate"
    collections = [offerings > 0, conversions > 0, value_props > 0]
    completeness = 0.6 * (facets_covered / facets_total if facets_total else 0) + 0.4 * (sum(collections) / 3)
    return ProfileQuality(
        claims_total=verified_facts + dropped_facts,
        claims_verified=verified_facts,
        claims_dropped_unverified=dropped_facts,
        injection_flags=injection_flags,
        thin_site=thin,
        label=label,  # type: ignore[arg-type]
        completeness=round(min(1.0, completeness), 3),
    )
