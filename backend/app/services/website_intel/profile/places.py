import re
from collections.abc import Sequence

from app.domain.profile import Location, ValueProposition
from app.domain.web import CONFIRMATION_REQUIRED_SOURCES, ExtractionKind
from app.services.website_intel.profile.canonical import NAME_STOP, canonical_tokens
from app.services.website_intel.profile.claims import stable_id
from app.services.website_intel.profile.market import CITIES, COUNTRY_NAMES, REGIONS

# "dispatch anywhere in india", "we deliver across the country": a reach, not a site
SERVICE_AREA = re.compile(
    r"\b(?:dispatch|deliver|delivery|ship|shipping|serve|serving|supply|supplies|available)\w*\b.*"
    r"\b(?:anywhere|across|all\s+over|throughout|nationwide|worldwide|pan[\s-]?india|every\s+(?:city|state))\b",
    re.IGNORECASE,
)
# two-letter names ("us", "uk") are left out: "visit us" is not a country
_PLACES = {**REGIONS, **CITIES, **{name: code for name, code in COUNTRY_NAMES.items() if len(name) > 2}}


def stated_country(item: Location) -> str | None:
    """the country a location names: its own field, a country name in its text, or a known city or state."""
    if item.country:
        return item.country
    text = " ".join(
        canonical_tokens(" ".join(filter(None, (item.name, item.address, item.city, item.region))))
    )
    found = {country for name, country in _PLACES.items() if re.search(rf"\b{re.escape(name)}\b", text)}
    return found.pop() if len(found) == 1 else None


def third_party(item: Location) -> bool:
    evidence = item.meta.evidence
    return bool(evidence) and all(entry.source_kind in CONFIRMATION_REQUIRED_SOURCES for entry in evidence)


def foreign_mentions(items: Sequence[Location], market: str | None) -> list[Location]:
    """drops third-party locations in another country: a similarly named company, not this business."""
    if market is None:
        return list(items)
    return [item for item in items if not (third_party(item) and (stated_country(item) or market) != market)]


def _bare_region(item: Location) -> bool:
    name = " ".join(token for token in canonical_tokens(item.name) if token not in NAME_STOP)
    return name in REGIONS and not item.address


def _url_slug_only(item: Location) -> bool:
    # a /packaging-in-bihar landing page targets a city; it does not say the business is there
    return all(entry.extraction is ExtractionKind.url_inventory for entry in item.meta.evidence)


def physical_sites(
    items: Sequence[Location], *, business_name: str
) -> tuple[list[Location], list[ValueProposition]]:
    """keeps physical sites and cities with a stated presence; reach statements become value props."""
    business = canonical_tokens(business_name)
    kept, reach = [], []
    for item in items:
        if item.kind == "service_area" or SERVICE_AREA.search(item.name):
            reach.append(
                ValueProposition(
                    id=stable_id("vp", item.name),
                    statement=item.name[:200],
                    kind="accessibility",
                    meta=item.meta,
                )
            )
            continue
        if _url_slug_only(item) or _bare_region(item):
            continue
        if canonical_tokens(item.name) == business:
            place = ", ".join(value for value in (item.city, item.region) if value)
            if not place:
                continue
            # the business name alone says nothing about which site this is; the address does
            item = item.model_copy(update={"name": place, "aliases": [item.name, *item.aliases][:10]})
        kept.append(item)
    return kept, reach
