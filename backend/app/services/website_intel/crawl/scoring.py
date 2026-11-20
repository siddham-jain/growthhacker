import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from app.services.website_intel.crawl.templates import TemplateClustering, apply_saturation_caps
from app.services.website_intel.models import PageType, UrlCandidate

TYPE_WEIGHT: dict[PageType, float] = {
    PageType.home: 1.0,
    PageType.about: 0.9,
    PageType.offering_hub: 0.95,
    PageType.pricing: 0.95,
    PageType.faq: 0.85,
    PageType.booking: 0.85,
    PageType.contact: 0.8,
    PageType.locations_hub: 0.8,
    PageType.offering_detail: 0.7,
    PageType.case_study: 0.75,
    PageType.news_press: 0.45,
    PageType.team_hub: 0.55,
    PageType.blog_index: 0.5,
    PageType.knowledge_library: 0.3,
    PageType.blog_post: 0.35,
    PageType.location_detail: 0.4,
    PageType.team_member: 0.15,
    PageType.investor: 0.2,
    PageType.careers: 0.05,
    PageType.legal: 0.05,
    PageType.other: 0.25,
}


@dataclass(frozen=True)
class ScoreWeights:
    type: float = 0.35
    nav: float = 0.20
    home: float = 0.12
    inlinks: float = 0.10
    depth: float = 0.08
    focus: float = 0.08
    recency: float = 0.04
    sitemap_priority: float = 0.03
    language_alternate_penalty: float = 0.3
    template_saturated_penalty: float = 0.5
    pdf_penalty: float = 0.2
    old_blog_post_penalty: float = 0.4
    old_blog_post_days: int = 730
    type_weight: dict[PageType, float] = field(default_factory=lambda: dict(TYPE_WEIGHT))


def nav_signal(nav_depth: int | None) -> float:
    if nav_depth is None:
        return 0.0
    if nav_depth <= 1:
        return 1.0
    return 0.6 if nav_depth == 2 else 0.3


class PriorityScorer:
    """score(u) = sum of weighted signals minus penalties (plan 03 §5.5)."""

    def __init__(self, weights: ScoreWeights | None = None) -> None:
        self.weights = weights or ScoreWeights()

    def score(self, candidate: UrlCandidate, *, max_inlinks: int, now: datetime) -> float:
        weights = self.weights
        age_days = (now - candidate.lastmod).days if candidate.lastmod is not None else None
        recency = math.exp(-max(age_days, 0) / 365) if age_days is not None else 0.5
        inlinks = math.log1p(candidate.inlinks) / math.log1p(max_inlinks) if max_inlinks > 0 else 0.0
        sitemap_priority = candidate.sitemap_priority if candidate.sitemap_priority is not None else 0.5
        total = (
            weights.type * weights.type_weight.get(candidate.page_type, TYPE_WEIGHT[PageType.other])
            + weights.nav * nav_signal(candidate.nav_depth)
            + weights.home * float(candidate.linked_from_home)
            + weights.inlinks * min(inlinks, 1.0)
            + weights.depth * (1 / (1 + candidate.depth))
            + weights.focus * float(candidate.under_focus)
            + weights.recency * recency
            + weights.sitemap_priority * sitemap_priority
        )
        if candidate.language_alternate:
            total -= weights.language_alternate_penalty
        if candidate.extra.get("template_saturated"):
            total -= weights.template_saturated_penalty
        if urlsplit(candidate.url).path.lower().endswith(".pdf"):
            total -= weights.pdf_penalty
        if (
            candidate.page_type is PageType.blog_post
            and age_days is not None
            and age_days > weights.old_blog_post_days
        ):
            total -= weights.old_blog_post_penalty
        return total


@dataclass
class Selection:
    mandatory: list[UrlCandidate]
    ranked: list[UrlCandidate]

    @property
    def all(self) -> list[UrlCandidate]:
        return self.mandatory + self.ranked


@dataclass(frozen=True)
class MandatoryQuota:
    conversion_pages: int = 5
    nav_offering_hubs: int = 8
    location_hubs: int = 2


CONVERSION_TYPES = frozenset({PageType.pricing, PageType.booking, PageType.faq})


class DeterministicSelector:
    """mandatory pages first, then score order under per-template caps; the llm-free fallback (plan 03 §5.8)."""

    def __init__(self, quota: MandatoryQuota | None = None) -> None:
        self.quota = quota or MandatoryQuota()

    def select(
        self,
        candidates: Sequence[UrlCandidate],
        *,
        budget: int,
        per_template_cap: int,
        clustering: TemplateClustering,
    ) -> Selection:
        ordered = sorted(candidates, key=lambda candidate: (-candidate.score, candidate.url))
        mandatory = self._mandatory(ordered)[:budget]
        chosen = {candidate.url for candidate in mandatory}
        rest = [candidate for candidate in ordered if candidate.url not in chosen]
        capped = apply_saturation_caps(
            mandatory + rest, per_template_cap=per_template_cap, clustering=clustering
        )
        ranked = [candidate for candidate in capped if candidate.url not in chosen]
        return Selection(mandatory=mandatory, ranked=ranked[: max(budget - len(mandatory), 0)])

    def _mandatory(self, ordered: list[UrlCandidate]) -> list[UrlCandidate]:
        quota = self.quota
        picks: list[UrlCandidate] = []
        for page_type in (PageType.home, PageType.about, PageType.contact):
            first = next((candidate for candidate in ordered if candidate.page_type is page_type), None)
            if first is not None:
                picks.append(first)
        picks += [candidate for candidate in ordered if candidate.page_type in CONVERSION_TYPES][
            : quota.conversion_pages
        ]
        picks += [
            candidate
            for candidate in ordered
            if candidate.page_type is PageType.offering_hub and candidate.nav_depth is not None
        ][: quota.nav_offering_hubs]
        picks += [candidate for candidate in ordered if candidate.page_type is PageType.locations_hub][
            : quota.location_hubs
        ]
        return list({candidate.url: candidate for candidate in picks}.values())
