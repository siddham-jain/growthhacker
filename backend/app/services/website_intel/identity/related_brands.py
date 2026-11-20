import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.profile import Evidence
from app.domain.web import ExtractionKind
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.inputs.urls import (
    InvalidUrlError,
    host_of,
    normalize_url,
    registrable_domain,
)
from app.services.website_intel.models import ExtractedPage

GROUP_LABELS = re.compile(
    r"group (?:brands|companies)|our brands|subsidiaries|sister concerns|हमारे ब्रांड", re.I
)
MIN_TOKEN_CHARS = 4
MAX_ANCHOR_NAME_CHARS = 48
GENERIC_ANCHORS = re.compile(r"^(?:visit|view|click|read|learn|know|go to|explore|more|here)\b", re.I)
Relation = Literal["parent", "sister", "subsidiary", "unknown"]
JSON_LD_RELATIONS: dict[str, Relation] = {
    "subOrganization": "subsidiary",
    "parentOrganization": "parent",
    "brand": "sister",
}


@dataclass(frozen=True)
class BrandSignal:
    name: str
    domain: str | None
    relation: Relation
    evidence: Evidence


def brand_name(anchor: str, label: str) -> str:
    text = " ".join(anchor.split())
    if (
        not text
        or "\n" in anchor.strip()
        or len(text) > MAX_ANCHOR_NAME_CHARS
        or GENERIC_ANCHORS.search(text)
    ):
        return label.title()
    return text


def name_tokens(name: str | None) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(word) >= MIN_TOKEN_CHARS]


def brand_token(name: str | None, domain: str) -> str:
    label = domain.split(".")[0].lower()
    words = name_tokens(name)
    if words and label.startswith(words[0]):
        return words[0]
    return label


def _domain(url: str) -> str | None:
    try:
        return registrable_domain(host_of(normalize_url(url)))
    except InvalidUrlError:
        return None


class RelatedBrandDetector:
    """sister and parent brands from outbound links, json-ld and the knowledge base; never crawled."""

    def detect(
        self,
        pages: Sequence[ExtractedPage],
        *,
        business_name: str | None,
        own_domain: str,
        knowledge: KbEntity | None = None,
        knowledge_page: ExtractedPage | None = None,
    ) -> list[BrandSignal]:
        token = brand_token(business_name, own_domain)
        found: dict[str, BrandSignal] = {}
        for page in pages:
            in_group_section = any(GROUP_LABELS.search(item.label) for item in page.details.nav)
            for link in page.details.links:
                domain = _domain(link.url)
                if domain is None or domain == own_domain or domain in found:
                    continue
                label = domain.split(".")[0]
                if len(token) < MIN_TOKEN_CHARS or not label.startswith(token):
                    continue
                found[domain] = BrandSignal(
                    name=brand_name(link.anchor, label),
                    domain=domain,
                    relation="sister" if in_group_section else "unknown",
                    evidence=self._evidence(page, link.anchor.strip() or link.url, ExtractionKind.nav),
                )
            for block in page.page.json_ld:
                self._json_ld(block, page, own_domain, found)
        if knowledge is not None and knowledge_page is not None:
            for relation, items in (("subsidiary", knowledge.subsidiaries), ("parent", knowledge.parents)):
                for item in items:
                    domain = _domain(item.website) if item.website else None
                    key = domain or item.name.casefold()
                    if key in found or domain == own_domain:
                        continue
                    found[key] = BrandSignal(
                        name=item.name,
                        domain=domain,
                        relation=relation,  # type: ignore[arg-type]
                        evidence=self._evidence(knowledge_page, item.name, ExtractionKind.page_text),
                    )
        return list(found.values())

    def _json_ld(
        self, block: dict[str, Any], page: ExtractedPage, own_domain: str, found: dict[str, BrandSignal]
    ) -> None:
        for prop, relation in JSON_LD_RELATIONS.items():
            values = block.get(prop)
            for value in values if isinstance(values, list) else [values]:
                if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                    continue
                url = value.get("url") if isinstance(value.get("url"), str) else None
                domain = _domain(url) if url else None
                key = domain or value["name"].casefold()
                if key in found or domain == own_domain:
                    continue
                found[key] = BrandSignal(
                    name=value["name"],
                    domain=domain,
                    relation=relation,
                    evidence=self._evidence(page, value["name"], ExtractionKind.json_ld),
                )

    @staticmethod
    def _evidence(page: ExtractedPage, quote: str, extraction: ExtractionKind) -> Evidence:
        return Evidence(
            page_id=page.page.page_id,
            source_kind=page.page.source_kind,
            extraction=extraction,
            source_url=page.page.url,
            quote=quote[:300],
            match_score=100,
            fetched_at=page.page.fetched_at,
            snapshot_at=page.page.snapshot_at,
        )
