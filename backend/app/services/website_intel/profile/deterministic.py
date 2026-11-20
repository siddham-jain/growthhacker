import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.domain.profile import Evidence, FactKind
from app.domain.web import ExtractionKind, SourceKind
from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import (
    CtaCandidate,
    ExtractedPage,
    InventoryUrl,
    PageType,
    PriceCandidate,
    TemplateSummary,
)
from app.services.website_intel.profile.evidence import FactStore

ORGANIZATION_SUFFIXES = (
    "Organization",
    "Business",
    "Hospital",
    "Clinic",
    "Store",
    "Restaurant",
    "Agency",
    "Corporation",
)
LOCAL_BUSINESS_TYPES = frozenset(
    {
        "Dentist",
        "Pharmacy",
        "Optician",
        "Attorney",
        "AutoDealer",
        "Hotel",
        "RealEstateAgent",
        "Plumber",
        "Electrician",
        "School",
    }
)
# individual practitioners are person data; only their count is ever used
PERSON_TYPES = frozenset({"Physician", "Person"})
OFFERING_NAV_TYPES = frozenset({PageType.offering_hub, PageType.offering_detail, PageType.pricing})
KB_LINES = {
    "Industry": FactKind.entity,
    "Headquarters": FactKind.location,
    "Products": FactKind.offering,
    "Subsidiaries": FactKind.brand_term,
    "Also known as": FactKind.brand_term,
}
MAX_NAV_OFFERINGS = 60
MAX_CTA_ROWS = 150
MAX_INVENTORY_LOCATIONS = 60
_SLUG_SPLIT = re.compile(r"[-_]+")
_TITLE_SEPARATOR = re.compile(r"\s[|\-–—:]\s")
SITE_SOURCES = frozenset(
    {
        SourceKind.site_direct,
        SourceKind.site_rendered,
        SourceKind.reader_proxy,
        SourceKind.search_index,
        SourceKind.archive,
    }
)


@dataclass
class CtaRow:
    cta_id: str
    candidate: CtaCandidate
    pages: set[str] = field(default_factory=set)
    on_home: bool = False
    in_header: bool = False
    in_footer: bool = False
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class PriceRow:
    price_id: str
    candidate: PriceCandidate
    evidence: Evidence


@dataclass
class DeterministicTables:
    ctas: dict[str, CtaRow] = field(default_factory=dict)
    prices: dict[str, PriceRow] = field(default_factory=dict)
    location_values: list[str] = field(default_factory=list)
    slug_vocabulary: list[str] = field(default_factory=list)


def _types(block: dict[str, Any]) -> list[str]:
    value = block.get("@type")
    return [str(item) for item in value] if isinstance(value, list) else [str(value)] if value else []


def cta_group_key(candidate: CtaCandidate) -> str:
    """mega-menus repeat one booking flow per specialty; group web targets by their first path segment."""
    if candidate.medium != "web" or not candidate.target.startswith("http"):
        return candidate.key
    parts = urlsplit(candidate.target)
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) <= 1:
        return candidate.key
    return f"{candidate.type}|{parts.scheme}://{parts.netloc}/{segments[0]}/*"


def humanise_slug(slug: str) -> str:
    return " ".join(_SLUG_SPLIT.split(slug.strip("/"))).strip()


def _evidence(page: ExtractedPage, quote: str, extraction: ExtractionKind) -> Evidence:
    return Evidence(
        page_id=page.page.page_id,
        source_kind=page.page.source_kind,
        extraction=extraction,
        source_url=page.page.url,
        quote=quote[:300],
        quote_lang=page.page.lang,
        match_score=100,
        fetched_at=page.page.fetched_at,
        snapshot_at=page.page.snapshot_at,
    )


class DeterministicFactBuilder:
    """facts that need no llm: json-ld, nav taxonomy, cta and price tables, url inventory, kb and channel lines."""

    def __init__(self, page_types: PageTypeClassifier | None = None) -> None:
        self._page_types = page_types or PageTypeClassifier()

    def build(
        self,
        pages: Sequence[ExtractedPage],
        store: FactStore,
        *,
        inventory: Sequence[InventoryUrl],
        templates: Sequence[TemplateSummary],
    ) -> DeterministicTables:
        tables = DeterministicTables()
        for page in pages:
            self._json_ld(page, store)
            self._identity_meta(page, store)
            if page.page.source_kind is SourceKind.knowledge_base:
                self._knowledge_lines(page, store)
            if page.page.source_kind is SourceKind.youtube_channel:
                self._channel_keywords(page, store)
            self._ctas(page, tables)
            self._prices(page, tables)
        self._nav_offerings(pages, store)
        self._inventory(pages, inventory, templates, store, tables)
        return tables

    def _identity_meta(self, page: ExtractedPage, store: FactStore) -> None:
        source = page.page.source_kind
        if source is SourceKind.knowledge_base and page.page.title:
            store.add(
                page,
                kind=FactKind.brand_term,
                text=page.page.title,
                quote=page.page.title,
                extraction=ExtractionKind.page_text,
                attrs={"role": "kb_label"},
            )
            return
        if source is SourceKind.youtube_channel and page.page.title:
            store.add(
                page,
                kind=FactKind.brand_term,
                text=page.page.title,
                quote=page.page.title,
                extraction=ExtractionKind.channel_metadata,
                attrs={"role": "channel_title"},
            )
            return
        if source not in SITE_SOURCES or path_of(page.page.url) != "/":
            return
        site_name = page.details.meta.get("og:site_name", "").strip()
        if site_name:
            store.add(
                page,
                kind=FactKind.brand_term,
                text=site_name,
                quote=site_name,
                extraction=ExtractionKind.meta,
                attrs={"role": "site_name"},
            )
        if page.page.title:
            segment = _TITLE_SEPARATOR.split(page.page.title)[0].strip()
            if segment:
                store.add(
                    page,
                    kind=FactKind.brand_term,
                    text=segment,
                    quote=segment,
                    extraction=ExtractionKind.meta,
                    attrs={"role": "title"},
                )
        description = page.details.meta.get("description", "").strip()
        if description:
            store.add(
                page,
                kind=FactKind.value_prop,
                text=description[:160],
                quote=description[:300],
                extraction=ExtractionKind.meta,
                attrs={"role": "meta_description"},
            )

    def _json_ld(self, page: ExtractedPage, store: FactStore) -> None:
        for block in page.page.json_ld:
            types = _types(block)
            is_organization = any(
                kind.endswith(ORGANIZATION_SUFFIXES) or kind in LOCAL_BUSINESS_TYPES for kind in types
            )
            if is_organization and not PERSON_TYPES & set(types):
                for prop in ("name", "legalName", "alternateName"):
                    if isinstance(block.get(prop), str) and block[prop].strip():
                        store.add(
                            page,
                            kind=FactKind.brand_term,
                            text=block[prop].strip(),
                            quote=block[prop].strip(),
                            extraction=ExtractionKind.json_ld,
                            attrs={"role": prop},
                        )
                address = block.get("address")
                for item in address if isinstance(address, list) else [address]:
                    if isinstance(item, dict):
                        self._address(page, store, item, block.get("name"))
            if "FAQPage" in types:
                self._faq(page, store, block)

    def _address(self, page: ExtractedPage, store: FactStore, address: dict[str, Any], name: Any) -> None:
        parts = [
            str(address.get(key, "")).strip() for key in ("streetAddress", "addressLocality", "addressRegion")
        ]
        line = ", ".join(part for part in parts if part)
        if not line:
            return
        store.add(
            page,
            kind=FactKind.location,
            text=str(name or address.get("addressLocality") or line)[:160],
            quote=line,
            extraction=ExtractionKind.json_ld,
            attrs={
                "address": line,
                "city": str(address.get("addressLocality", "")),
                "region": str(address.get("addressRegion", "")),
                "country": str(address.get("addressCountry", ""))[:2].upper(),
            },
        )

    def _faq(self, page: ExtractedPage, store: FactStore, block: dict[str, Any]) -> None:
        entities = block.get("mainEntity")
        for item in entities if isinstance(entities, list) else [entities]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            answer = item.get("acceptedAnswer")
            answer_text = answer.get("text", "") if isinstance(answer, dict) else ""
            question = item["name"].strip()
            store.add(
                page,
                kind=FactKind.faq,
                text=question,
                quote=question,
                extraction=ExtractionKind.json_ld,
                attrs={"question": question, "answer_quote": re.sub(r"<[^>]+>", " ", str(answer_text))[:300]},
            )

    def _knowledge_lines(self, page: ExtractedPage, store: FactStore) -> None:
        for line in page.page.text.splitlines():
            label, _, value = line.partition(": ")
            kind = KB_LINES.get(label)
            if kind is None or not value:
                continue
            for item in value.split(", "):
                store.add(
                    page,
                    kind=kind,
                    text=item.strip()[:160],
                    quote=item.strip(),
                    extraction=ExtractionKind.page_text,
                    attrs={"kb_field": label},
                )

    def _channel_keywords(self, page: ExtractedPage, store: FactStore) -> None:
        for line in page.page.text.splitlines():
            if line.startswith("Channel keywords: "):
                for keyword in line.removeprefix("Channel keywords: ").split(", "):
                    if keyword.strip():
                        store.add(
                            page,
                            kind=FactKind.keyword_phrase,
                            text=keyword.strip(),
                            quote=keyword.strip(),
                            extraction=ExtractionKind.channel_metadata,
                            attrs={"source": "youtube_channel_keywords"},
                        )

    def _ctas(self, page: ExtractedPage, tables: DeterministicTables) -> None:
        is_home = path_of(page.page.url) == "/"
        for candidate in page.details.ctas:
            key = cta_group_key(candidate)
            row = tables.ctas.get(key)
            if row is None:
                if len(tables.ctas) >= MAX_CTA_ROWS:
                    continue
                row = CtaRow(cta_id=f"C{len(tables.ctas) + 1}", candidate=candidate)
                tables.ctas[key] = row
            row.pages.add(page.page.url)
            row.on_home = row.on_home or is_home
            row.in_header = row.in_header or candidate.in_header
            row.in_footer = row.in_footer or candidate.in_footer
            if len(row.evidence) < 3:
                row.evidence.append(
                    _evidence(page, candidate.label or candidate.target, ExtractionKind.page_text)
                )

    def _prices(self, page: ExtractedPage, tables: DeterministicTables) -> None:
        for candidate in page.details.prices:
            price_id = f"${len(tables.prices) + 1}"
            extraction = ExtractionKind.json_ld if candidate.origin == "json_ld" else ExtractionKind.page_text
            tables.prices[price_id] = PriceRow(
                price_id, candidate, _evidence(page, candidate.raw_text, extraction)
            )

    def _nav_offerings(self, pages: Sequence[ExtractedPage], store: FactStore) -> None:
        added = 0
        for page in pages:
            for item in page.details.nav:
                if added >= MAX_NAV_OFFERINGS:
                    return
                if self._page_types.classify(item.url, anchor=item.label) not in OFFERING_NAV_TYPES:
                    continue
                if len(item.label) < 3 or len(item.label) > 80:
                    continue
                store.add(
                    page,
                    kind=FactKind.offering,
                    text=item.label,
                    quote=item.label,
                    extraction=ExtractionKind.nav,
                    attrs={"url": item.url, "nav_depth": str(item.depth)},
                )
                added += 1

    def _inventory(
        self,
        pages: Sequence[ExtractedPage],
        inventory: Sequence[InventoryUrl],
        templates: Sequence[TemplateSummary],
        store: FactStore,
        tables: DeterministicTables,
    ) -> None:
        anchor = next((page for page in pages if page.page.source_kind is SourceKind.site_direct), None)
        locations = [value for summary in templates for value in summary.location_values]
        tables.location_values = list(dict.fromkeys(locations))[:MAX_INVENTORY_LOCATIONS]
        slugs: list[str] = []
        for item in inventory:
            if item.page_type in OFFERING_NAV_TYPES or item.page_type is PageType.knowledge_library:
                leaf = path_of(item.url).rstrip("/").rsplit("/", 1)[-1]
                if leaf:
                    slugs.append(humanise_slug(leaf))
        tables.slug_vocabulary = list(dict.fromkeys(slugs))[:300]
        if anchor is None:
            return
        for value in tables.location_values:
            store.add(
                anchor,
                kind=FactKind.location,
                text=humanise_slug(value).title(),
                quote=value,
                extraction=ExtractionKind.url_inventory,
                attrs={"city": humanise_slug(value).title(), "origin": "url_inventory"},
            )


def home_urls(pages: Iterable[ExtractedPage]) -> frozenset[str]:
    return frozenset(page.page.url for page in pages if path_of(page.page.url) == "/")
