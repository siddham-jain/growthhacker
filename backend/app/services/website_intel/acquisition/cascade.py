import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.runs import SourceFallbackPayload
from app.domain.web import CoverageFacet, CrawlBudget, SourceKind, UserAgentMode
from app.ports.context import BlockedAttempt, ContextSource
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.coverage import REQUIRED_FACETS, CoverageScorer
from app.services.website_intel.acquisition.models import CascadeRequest, RobotsSnapshot, SourceHarvest
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import ExtractedPage, InventoryUrl, NavItem, TemplateSummary

SOURCE_LABELS: dict[SourceKind, str] = {
    SourceKind.site_direct: "the website",
    SourceKind.site_rendered: "a real browser",
    SourceKind.reader_proxy: "a reader service",
    SourceKind.search_index: "the search index",
    SourceKind.archive: "the web archive",
    SourceKind.knowledge_base: "Wikipedia and Wikidata",
    SourceKind.web_mentions: "third-party mentions",
    SourceKind.youtube_channel: "the YouTube channel",
    SourceKind.user: "your notes",
}


@dataclass
class TierReport:
    tier: int
    source_kind: SourceKind
    pages: int
    blocked: int
    covered: list[CoverageFacet]
    credits: float
    notes: list[str] = field(default_factory=list)


@dataclass
class CascadeOutcome:
    pages: list[ExtractedPage] = field(default_factory=list)
    blocked: list[BlockedAttempt] = field(default_factory=list)
    covered: frozenset[CoverageFacet] = frozenset()
    tiers: list[TierReport] = field(default_factory=list)
    inventory: list[InventoryUrl] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    templates: list[TemplateSummary] = field(default_factory=list)
    nav: list[NavItem] = field(default_factory=list)
    robots: RobotsSnapshot | None = None
    ua_mode: UserAgentMode = UserAgentMode.honest
    knowledge_entity: KbEntity | None = None
    tavily_credits: float = 0.0
    fetches: int = 0
    stats: Counter[str] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    @property
    def missing(self) -> frozenset[CoverageFacet]:
        return REQUIRED_FACETS - self.covered

    @property
    def sources_used(self) -> dict[SourceKind, int]:
        return dict(Counter(page.page.source_kind for page in self.pages))


class AcquisitionCascade:
    """runs ContextSource tiers in order until every required facet is covered (plan 03 §14)."""

    def __init__(self, sources: Sequence[ContextSource], scorer: CoverageScorer | None = None) -> None:
        self._sources = sorted(sources, key=lambda source: source.tier)
        self._scorer = scorer or CoverageScorer()

    async def run(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> CascadeOutcome:
        outcome = CascadeOutcome()
        seen_urls: set[str] = set()
        previous: SourceKind | None = None
        for source in self._sources:
            request = self._advance(request, outcome)
            if not source.applicable(request):
                continue
            remaining = self._remaining(budget, outcome)
            if remaining.max_tavily_credits <= 0 and source.source_kind in {
                SourceKind.search_index,
                SourceKind.web_mentions,
            }:
                outcome.notes.append(f"{source.source_kind.value}: credit budget spent")
                continue
            if previous is not None and outcome.missing:
                await reporter.step(
                    "crawl.fallback",
                    f"Still missing {', '.join(sorted(f.value for f in outcome.missing))}; "
                    f"trying {SOURCE_LABELS[source.source_kind]}",
                    SourceFallbackPayload(
                        from_source=previous.value,
                        to_source=source.source_kind.value,
                        reason=",".join(sorted(f.value for f in outcome.missing)),
                    ),
                )
            await reporter.step(
                "crawl.source_started", f"Tier {source.tier}: reading {SOURCE_LABELS[source.source_kind]}"
            )
            started = time.monotonic()
            result = await source.acquire(request, remaining, reporter)
            outcome.stats[f"{source.source_kind.value}_ms"] += int((time.monotonic() - started) * 1000)
            if not isinstance(result, SourceHarvest):
                raise TypeError(f"{source.source_kind.value} must return a SourceHarvest")
            self._merge(outcome, result, seen_urls)
            for attempt in result.blocked[:5]:
                await reporter.step(
                    "crawl.blocked", f"{path_of(attempt.url)} blocked ({attempt.reason.value})"
                )
            outcome.tiers.append(
                TierReport(
                    tier=source.tier,
                    source_kind=source.source_kind,
                    pages=len(result.pages),
                    blocked=len(result.blocked),
                    covered=sorted(self._scorer.facets(result.extracted) | result.covered_facets),
                    credits=result.tavily_credits,
                    notes=list(result.notes),
                )
            )
            previous = source.source_kind
        return outcome

    def _advance(self, request: CascadeRequest, outcome: CascadeOutcome) -> CascadeRequest:
        fetched = {page.page.url for page in outcome.pages}
        merged = list(dict.fromkeys([*request.candidate_urls, *outcome.candidates]))
        candidates = [url for url in merged if url not in fetched]
        return request.model_copy(
            update={
                "missing_facets": outcome.missing,
                "candidate_urls": candidates,
                "fetched_urls": sorted(fetched),
                "prior_blocked": outcome.blocked,
                "robots": outcome.robots or request.robots,
                "knowledge_entity": outcome.knowledge_entity or request.knowledge_entity,
                "home_covered": CoverageFacet.home in outcome.covered,
            }
        )

    @staticmethod
    def _remaining(budget: CrawlBudget, outcome: CascadeOutcome) -> CrawlBudget:
        return budget.model_copy(
            update={
                "max_tavily_credits": max(0, int(budget.max_tavily_credits - outcome.tavily_credits)),
                "max_fetches": max(0, budget.max_fetches - outcome.fetches),
            }
        )

    def _merge(self, outcome: CascadeOutcome, result: SourceHarvest, seen_urls: set[str]) -> None:
        fresh = [page for page in result.extracted if page.page.canonical_url not in seen_urls]
        seen_urls.update(page.page.canonical_url for page in fresh)
        outcome.pages.extend(fresh)
        outcome.blocked.extend(result.blocked)
        outcome.tavily_credits += result.tavily_credits
        outcome.fetches += result.fetches
        outcome.covered = outcome.covered | self._scorer.facets(fresh) | result.covered_facets
        outcome.inventory = outcome.inventory or result.inventory
        outcome.candidates = list(dict.fromkeys([*outcome.candidates, *result.remaining_urls]))
        outcome.templates = outcome.templates or result.templates
        outcome.nav = outcome.nav or result.nav
        outcome.robots = outcome.robots or result.robots
        outcome.knowledge_entity = outcome.knowledge_entity or result.knowledge_entity
        if result.ua_mode is UserAgentMode.compat:
            outcome.ua_mode = UserAgentMode.compat
        outcome.stats.update(result.stats)
        outcome.notes.extend(result.notes)
