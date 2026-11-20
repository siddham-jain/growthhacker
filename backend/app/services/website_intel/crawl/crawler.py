import asyncio
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from app.core.config import CrawlProfileSettings
from app.domain.runs import PageReadPayload, PageSkippedPayload
from app.domain.web import (
    BlockReason,
    CrawlBudget,
    CrawlRun,
    CrawlRunStatus,
    FetchResult,
    SiteRef,
    SourceKind,
    UserAgentMode,
)
from app.ports.clock import Clock
from app.ports.context import BlockedAttempt
from app.ports.crawler import FetchContext
from app.ports.progress import ProgressReporter
from app.services.website_intel.crawl.fetching import RawFetcher, RawResponse
from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.crawl.politeness import HostGovernor
from app.services.website_intel.crawl.robots import RobotsRules, effective_delay
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.crawl.scoring import DeterministicSelector, PriorityScorer
from app.services.website_intel.crawl.sitemaps import (
    DEFAULT_SITEMAP_PATHS,
    SitemapEntry,
    SitemapReader,
    order_child_sitemaps,
)
from app.services.website_intel.crawl.templates import TemplateClusterer, TemplateClustering
from app.services.website_intel.crawl.traps import TrapDetector
from app.services.website_intel.extract.fingerprint import hamming
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.inputs.ssrf import DnsLookupError, UnsafeTargetError
from app.services.website_intel.inputs.urls import (
    InvalidUrlError,
    dedupe_key,
    host_of,
    in_scope,
    normalize_url,
    path_of,
)
from app.services.website_intel.models import (
    ExtractedPage,
    InventoryUrl,
    NavItem,
    PageType,
    PageVerdict,
    TemplateSummary,
    UrlCandidate,
)

NEAR_DUPLICATE_BITS = 3
MAX_INVENTORY = 5_000
PDF_ANCHOR_HINTS = ("pricing", "price", "brochure", "rate card", "tariff", "packages", "package")
PAGE_READ_KIND = {
    PageType.offering_hub: "service",
    PageType.offering_detail: "service",
    PageType.about: "about",
    PageType.locations_hub: "location",
    PageType.location_detail: "location",
    PageType.pricing: "pricing",
    PageType.blog_post: "blog",
    PageType.blog_index: "blog",
}


class PageSelector(Protocol):
    # phase b: returns chosen urls (subset of candidates) or None to fall back to score order
    async def select(
        self,
        *,
        candidates: Sequence[UrlCandidate],
        mandatory: Sequence[UrlCandidate],
        budget: int,
        nav: Sequence[NavItem],
        templates: Sequence[TemplateSummary],
        business_name: str | None,
    ) -> list[str] | None: ...


@dataclass
class CrawlHarvest:
    site: SiteRef
    pages: list[ExtractedPage] = field(default_factory=list)
    blocked: list[BlockedAttempt] = field(default_factory=list)
    inventory: list[InventoryUrl] = field(default_factory=list)
    templates: list[TemplateSummary] = field(default_factory=list)
    nav: list[NavItem] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    robots: RobotsRules | None = None
    ua_mode: UserAgentMode = UserAgentMode.honest
    home_verdict: PageVerdict | None = None
    stats: Counter[str] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


@dataclass
class _CrawlState:
    site: SiteRef
    ctx: FetchContext
    harvest: CrawlHarvest
    candidates: dict[str, UrlCandidate] = field(default_factory=dict)
    fetched: set[str] = field(default_factory=set)
    content_hashes: set[str] = field(default_factory=set)
    simhashes: list[tuple[int, str]] = field(default_factory=list)
    per_parent: Counter[str] = field(default_factory=Counter)
    trailing_slash: bool | None = None


class PoliteCrawler:
    """robots → sitemaps + nav → frontier → selection → polite fetch; implements the SiteCrawler port."""

    def __init__(
        self,
        *,
        fetcher: RawFetcher,
        robots: RobotsService,
        governor: HostGovernor,
        extractor: PageExtractor,
        settings: CrawlProfileSettings,
        clock: Clock,
        page_types: PageTypeClassifier | None = None,
        scorer: PriorityScorer | None = None,
        selector: DeterministicSelector | None = None,
        page_selector: PageSelector | None = None,
        sitemap_reader: SitemapReader | None = None,
        traps: TrapDetector | None = None,
        clusterer: TemplateClusterer | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._robots = robots
        self._governor = governor
        self._extractor = extractor
        self._settings = settings
        self._clock = clock
        self._page_types = page_types or PageTypeClassifier()
        self._scorer = scorer or PriorityScorer()
        self._selector = selector or DeterministicSelector()
        self._page_selector = page_selector
        self._sitemaps = sitemap_reader or SitemapReader(max_urls_per_file=settings.max_sitemap_urls)
        self._traps = traps or TrapDetector()
        self._clusterer = clusterer or TemplateClusterer()

    async def crawl(
        self,
        site: SiteRef,
        budget: CrawlBudget,
        reporter: ProgressReporter,
        previous: CrawlRun | None = None,
    ) -> CrawlRun:
        started = self._clock.now()
        harvest = await self.harvest(site, budget, reporter)
        status = CrawlRunStatus.completed
        if not harvest.pages:
            status = CrawlRunStatus.blocked if harvest.blocked else CrawlRunStatus.failed
        elif harvest.blocked:
            status = CrawlRunStatus.partial
        return CrawlRun(
            crawl_run_id=uuid4(),
            site=site,
            trigger="onboarding" if previous is None else "incremental",
            profile="client",
            status=status,
            ua_mode=harvest.ua_mode,
            pages=[page.page for page in harvest.pages],
            sources_used=[SourceKind.site_direct] if harvest.pages else [],
            blocked=dict(Counter(attempt.reason.value for attempt in harvest.blocked)),
            stats=dict(harvest.stats),
            started_at=started,
            finished_at=self._clock.now(),
        )

    async def fetch_page(self, url: str, ctx: FetchContext) -> FetchResult:
        rules, mode = await self._robots.rules(url, ctx)
        if not rules.can_fetch(url):
            raise PermissionError(f"robots.txt disallows {url}")
        return self._fetcher.to_result(await self._fetch(url, ctx.model_copy(update={"ua_mode": mode})))

    async def harvest(
        self,
        site: SiteRef,
        budget: CrawlBudget,
        reporter: ProgressReporter,
        *,
        client_id: UUID | None = None,
        run_id: UUID | None = None,
        country_hint: str | None = None,
        business_name: str | None = None,
    ) -> CrawlHarvest:
        ctx = FetchContext(
            client_id=client_id,
            run_id=run_id,
            timeout_s=20.0,
            max_bytes=self._settings.max_html_bytes,
        )
        state = _CrawlState(site=site, ctx=ctx, harvest=CrawlHarvest(site=site))
        deadline = time.monotonic() + min(budget.wall_seconds, self._settings.phase_a_timeout_s)
        try:
            rules, mode = await self._robots.rules(site.canonical_origin, ctx)
        except (UnsafeTargetError, DnsLookupError) as error:
            state.harvest.notes.append(error.user_message)
            return state.harvest
        state.harvest.robots = rules
        state.harvest.ua_mode = mode
        state.ctx = ctx.model_copy(update={"ua_mode": mode})
        self._governor.set_delay(
            host_of(site.canonical_origin), effective_delay(rules.crawl_delay, self._settings.min_delay_s)
        )
        home_url = site.canonical_origin + (site.focus_path or "/")
        if not rules.can_fetch(site.canonical_origin + "/"):
            state.harvest.stats["robots_disallowed"] += 1
            state.harvest.notes.append("robots_disallow_all")
            await reporter.step("crawl.blocked", "robots.txt does not allow crawling this site")
            return state.harvest
        await reporter.step("crawl.discovering", f"Reading {site.registrable_domain}")
        home = await self._fetch_extract(state, site.canonical_origin + "/", reporter, country_hint)
        state.harvest.home_verdict = home[1]
        if home[0] is None:
            return state.harvest
        home_page = home[0]
        self._seed_from_home(state, home_page)
        if home_url != site.canonical_origin + "/":
            self._add_candidate(state, home_url, "home", depth=0, linked_from_home=True)
        await self._read_sitemaps(state, rules, deadline)
        await self._follow_links(state, reporter, budget, deadline, country_hint)
        await reporter.step("crawl.discovered", f"Found {len(state.candidates)} pages to consider")
        chosen = await self._choose(state, budget, business_name)
        await self._fetch_selected(state, chosen, reporter, budget, country_hint)
        await self._fetch_pdfs(state, reporter)
        self._finish_inventory(state)
        return state.harvest

    def _seed_from_home(self, state: _CrawlState, home: ExtractedPage) -> None:
        nav_depth: dict[str, int] = {}
        for item in home.details.nav:
            key = self._key(state, item.url)
            if key is not None:
                nav_depth[key] = min(nav_depth.get(key, item.depth), item.depth)
        state.harvest.nav = list(home.details.nav)
        same_host_langs = {
            path_of(alternate.url).strip("/").split("/")[0].lower()
            for alternate in home.details.hreflang
            if host_of(alternate.url) in state.site.scope_hosts and path_of(alternate.url) != "/"
        }
        state.harvest.stats["hreflang_alternates"] = len(home.details.hreflang)
        for link in home.details.links:
            candidate = self._add_candidate(state, link.url, "link", depth=1, anchor=link.anchor)
            if candidate is not None:
                candidate.linked_from_home = True
        for key, depth in nav_depth.items():
            candidate = self._add_candidate(state, key, "nav", depth=1)
            if candidate is not None:
                candidate.nav_depth = depth
                candidate.linked_from_home = True
        for candidate in state.candidates.values():
            first_segment = path_of(candidate.url).strip("/").split("/")[0].lower()
            candidate.language_alternate = bool(first_segment) and first_segment in same_host_langs

    def _key(self, state: _CrawlState, url: str) -> str | None:
        try:
            normalised = normalize_url(url)
        except InvalidUrlError:
            return None
        if not in_scope(normalised, state.site.scope_hosts):
            return None
        return dedupe_key(normalised, trailing_slash=state.trailing_slash)

    def _add_candidate(
        self,
        state: _CrawlState,
        url: str,
        source: str,
        *,
        depth: int,
        anchor: str = "",
        linked_from_home: bool = False,
        entry: SitemapEntry | None = None,
    ) -> UrlCandidate | None:
        key = self._key(state, url)
        if key is None:
            return None
        existing = state.candidates.get(key)
        if existing is not None:
            existing.inlinks += 1
            existing.anchor = existing.anchor or anchor[:80]
            if entry is not None and existing.lastmod is None:
                existing.lastmod = entry.lastmod
                existing.sitemap_priority = entry.priority
            return existing
        if len(state.candidates) >= self._settings.max_sitemap_urls:
            return None
        trap = self._traps.reason(key)
        if trap is not None:
            state.harvest.stats["traps"] += 1
            return None
        if state.harvest.robots is not None and not state.harvest.robots.can_fetch(key):
            state.harvest.stats["robots_disallowed"] += 1
            return None
        candidate = UrlCandidate(
            url=key,
            source=source,  # type: ignore[arg-type]
            depth=depth,
            anchor=anchor[:80],
            linked_from_home=linked_from_home,
            inlinks=1,
            lastmod=entry.lastmod if entry else None,
            sitemap_priority=entry.priority if entry else None,
            under_focus=bool(state.site.focus_path) and path_of(key).startswith(state.site.focus_path or ""),
        )
        candidate.page_type = self._page_types.classify(
            key, anchor=anchor, is_home=path_of(key) == "/" and source != "sitemap"
        )
        state.candidates[key] = candidate
        return candidate

    async def _read_sitemaps(self, state: _CrawlState, rules: RobotsRules, deadline: float) -> None:
        origin = state.site.canonical_origin
        queue = [SitemapEntry(url=url) for url in rules.sitemaps if in_scope(url, state.site.scope_hosts)]
        if not queue:
            queue = [SitemapEntry(url=origin + path) for path in DEFAULT_SITEMAP_PATHS]
        read_files = 0
        seen: set[str] = set()
        while queue and read_files < self._settings.max_sitemap_files and time.monotonic() < deadline:
            entry = queue.pop(0)
            if entry.url in seen:
                continue
            seen.add(entry.url)
            raw = await self._fetch(entry.url, state.ctx)
            read_files += 1
            if raw.status_code != 200 or not raw.body:
                continue
            parsed = self._sitemaps.parse(raw.body, url=entry.url, allowed_hosts=set(state.site.scope_hosts))
            state.harvest.stats["sitemap_off_host"] += parsed.dropped_off_host
            for url_entry in parsed.entries:
                self._add_candidate(state, url_entry.url, "sitemap", depth=2, entry=url_entry)
            state.harvest.stats["sitemap_urls"] += len(parsed.entries)
            remaining = self._settings.max_sitemap_files - read_files
            queue.extend(order_child_sitemaps(parsed.children, remaining))
        state.harvest.stats["sitemap_files"] = read_files
        slashes = Counter(path_of(url).endswith("/") for url in state.candidates if path_of(url) != "/")
        if slashes:
            state.trailing_slash = slashes.most_common(1)[0][0]

    async def _follow_links(
        self,
        state: _CrawlState,
        reporter: ProgressReporter,
        budget: CrawlBudget,
        deadline: float,
        country_hint: str | None,
    ) -> None:
        discovery_budget = min(self._settings.discovery_fetch_budget, budget.max_discovered)
        while len(state.fetched) < discovery_budget and time.monotonic() < deadline:
            frontier = [
                candidate
                for candidate in state.candidates.values()
                if candidate.url not in state.fetched
                and candidate.depth < self._settings.max_link_depth
                and candidate.source in {"nav", "link", "home"}
                and state.per_parent[parent_path(candidate.url)] < self._settings.per_template_cap
            ]
            if not frontier:
                return
            self._score_all(state)
            frontier.sort(key=lambda candidate: (-candidate.score, candidate.url))
            batch = []
            for candidate in frontier:
                parent = parent_path(candidate.url)
                if state.per_parent[parent] < self._settings.per_template_cap:
                    state.per_parent[parent] += 1
                    batch.append(candidate)
                if len(batch) >= max(1, min(self._settings.max_concurrency_per_host, discovery_budget)):
                    break
            results = await asyncio.gather(
                *(self._fetch_extract(state, candidate.url, reporter, country_hint) for candidate in batch)
            )
            for candidate, (page, _) in zip(batch, results, strict=True):
                if page is None:
                    continue
                for link in page.details.links:
                    self._add_candidate(
                        state, link.url, "link", depth=candidate.depth + 1, anchor=link.anchor
                    )

    def _score_all(self, state: _CrawlState) -> None:
        now = self._clock.now()
        max_inlinks = max((candidate.inlinks for candidate in state.candidates.values()), default=1)
        for candidate in state.candidates.values():
            candidate.score = self._scorer.score(candidate, max_inlinks=max_inlinks, now=now)

    async def _choose(self, state: _CrawlState, budget: CrawlBudget, business_name: str | None) -> list[str]:
        self._score_all(state)
        open_candidates = [c for c in state.candidates.values() if c.url not in state.fetched]
        clustering = self._clusterer.cluster([candidate.url for candidate in state.candidates.values()])
        self._apply_templates(state, clustering)
        remaining_budget = max(0, min(self._settings.fetch_budget, budget.max_fetches) - len(state.fetched))
        selection = self._selector.select(
            open_candidates,
            budget=remaining_budget,
            per_template_cap=self._settings.per_template_cap,
            clustering=clustering,
        )
        chosen = [candidate.url for candidate in selection.all]
        if self._page_selector is not None and remaining_budget > len(selection.mandatory):
            picked = await self._page_selector.select(
                candidates=selection.ranked
                + [c for c in open_candidates if c.url not in set(chosen)][
                    : self._settings.selection_candidates
                ],
                mandatory=selection.mandatory,
                budget=remaining_budget - len(selection.mandatory),
                nav=state.harvest.nav,
                templates=state.harvest.templates,
                business_name=business_name,
            )
            if picked is not None:
                mandatory_urls = [candidate.url for candidate in selection.mandatory]
                llm_urls = [url for url in picked if url in state.candidates and url not in mandatory_urls]
                topped_up = llm_urls + [c.url for c in selection.ranked if c.url not in set(llm_urls)]
                chosen = mandatory_urls + topped_up[: remaining_budget - len(mandatory_urls)]
        ranked_rest = sorted(open_candidates, key=lambda candidate: (-candidate.score, candidate.url))
        state.harvest.remaining = [c.url for c in ranked_rest if c.url not in set(chosen)][:200]
        return chosen

    def _apply_templates(self, state: _CrawlState, clustering: TemplateClustering) -> None:
        state.harvest.templates = clustering.summaries
        counts = Counter(clustering.template_of.values())
        for url, candidate in state.candidates.items():
            candidate.template_key = clustering.template_of.get(url)
            if (
                candidate.template_key
                and counts[candidate.template_key] > self._settings.per_template_cap * 10
            ):
                candidate.extra["template_saturated"] = True

    async def _fetch_selected(
        self,
        state: _CrawlState,
        urls: list[str],
        reporter: ProgressReporter,
        budget: CrawlBudget,
        country_hint: str | None,
    ) -> None:
        deadline = time.monotonic() + min(self._settings.phase_c_timeout_s, budget.wall_seconds)
        todo = [url for url in urls if url not in state.fetched]
        total = len(todo) + len(state.fetched)
        workers = max(1, self._settings.max_concurrency_per_host)
        queue: asyncio.Queue[str] = asyncio.Queue()
        for url in todo:
            queue.put_nowait(url)

        async def worker() -> None:
            while not queue.empty() and time.monotonic() < deadline:
                if self._governor.stopped(host_of(state.site.canonical_origin)):
                    return
                url = queue.get_nowait()
                await self._fetch_extract(state, url, reporter, country_hint)
                await reporter.progress(len(state.fetched), total, "pages")

        await asyncio.gather(*(worker() for _ in range(workers)))
        skipped = [url for url in todo if url not in state.fetched]
        if skipped:
            state.harvest.stats["budget_skipped"] += len(skipped)
            state.harvest.remaining = skipped + state.harvest.remaining

    async def _fetch_pdfs(self, state: _CrawlState, reporter: ProgressReporter) -> None:
        fetched = 0
        for page in list(state.harvest.pages):
            for link in page.details.pdf_links:
                if fetched >= self._settings.pdf_budget:
                    return
                if not any(hint in link.anchor.lower() for hint in PDF_ANCHOR_HINTS):
                    continue
                key = self._key(state, link.url)
                if key is None or key in state.fetched:
                    continue
                fetched += 1
                await self._fetch_extract(state, key, reporter, None)

    async def _fetch(self, url: str, ctx: FetchContext) -> RawResponse:
        host = host_of(url)
        raw = await self._governed(url, ctx, host)
        retry_wait = self._governor.record(host, raw.status_code, raw.retry_after_s)
        attempts = 0
        while retry_wait is not None and attempts < 2:
            attempts += 1
            await asyncio.sleep(retry_wait)
            raw = await self._governed(url, ctx, host)
            retry_wait = self._governor.record(host, raw.status_code, raw.retry_after_s)
        if raw.verdict in {PageVerdict.server_error, PageVerdict.error} and raw.status_code != 404:
            for backoff in (2.0, 6.0):
                if raw.verdict not in {PageVerdict.server_error, PageVerdict.error}:
                    break
                await asyncio.sleep(backoff)
                raw = await self._governed(url, ctx, host)
        return raw

    async def _governed(self, url: str, ctx: FetchContext, host: str) -> RawResponse:
        async with self._governor.slot(host):
            return await self._fetcher.fetch_raw(url, ctx)

    async def _fetch_extract(
        self, state: _CrawlState, url: str, reporter: ProgressReporter, country_hint: str | None
    ) -> tuple[ExtractedPage | None, PageVerdict]:
        state.fetched.add(url)
        try:
            raw = await self._fetch(url, state.ctx)
        except (UnsafeTargetError, DnsLookupError) as error:
            state.harvest.notes.append(error.user_message)
            return None, PageVerdict.error
        if raw.status_code in {401, 403} and state.ctx.ua_mode is UserAgentMode.honest:
            # the only identity change we make: the compat agent still carries our bot token
            state.ctx = state.ctx.model_copy(update={"ua_mode": UserAgentMode.compat})
            state.harvest.ua_mode = UserAgentMode.compat
            state.harvest.stats["ua_compat_switch"] += 1
            raw = await self._fetch(url, state.ctx)
        state.harvest.stats[f"verdict_{raw.verdict.value}"] += 1
        state.harvest.stats["fetched"] += 1
        if raw.verdict is not PageVerdict.ok:
            await self._record_failure(state, url, raw.verdict, reporter)
            return None, raw.verdict
        page = self._build_page(state, raw, country_hint)
        if page is None:
            return None, PageVerdict.ok
        if not self._is_new_content(state, page):
            state.harvest.stats["duplicates"] += 1
            await reporter.insight(
                "Skipped a duplicate page", PageSkippedPayload(url=url, reason="duplicate")
            )
            return None, PageVerdict.ok
        state.harvest.pages.append(page)
        kind = PAGE_READ_KIND.get(PageType(page.page.page_type or "other"), "other")
        await reporter.insight(
            f"Read {path_of(page.page.url)}",
            PageReadPayload(
                url=page.page.url,
                path=path_of(page.page.url),
                title=page.page.title,
                page_type=kind,  # type: ignore[arg-type]
            ),
        )
        return page, PageVerdict.ok

    async def _record_failure(
        self, state: _CrawlState, url: str, verdict: PageVerdict, reporter: ProgressReporter
    ) -> None:
        reason = verdict.block_reason
        if reason is not None:
            state.harvest.blocked.append(
                BlockedAttempt(url=url, source_kind=SourceKind.site_direct, reason=reason)
            )
            if reason is not BlockReason.empty_shell:
                await reporter.step("crawl.blocked", f"{path_of(url)} blocked ({reason.value})")
            await reporter.insight("Page blocked", PageSkippedPayload(url=url, reason="blocked"))
        elif verdict in {PageVerdict.error, PageVerdict.server_error}:
            await reporter.insight("Page timed out", PageSkippedPayload(url=url, reason="timeout"))

    def _build_page(
        self, state: _CrawlState, raw: RawResponse, country_hint: str | None
    ) -> ExtractedPage | None:
        result = self._fetcher.to_result(raw)
        if result.text and not result.html:
            page = self._extractor.from_text(
                text=result.text,
                url=raw.final_url,
                title=None,
                source_kind=SourceKind.site_direct,
                fetched_at=result.fetched_at,
                country_hint=country_hint,
            )
        elif result.html:
            page = self._extractor.from_html(
                html=result.html,
                requested_url=raw.requested_url,
                final_url=raw.final_url,
                site=state.site,
                source_kind=SourceKind.site_direct,
                status_code=raw.status_code,
                content_type=raw.content_type,
                fetched_at=result.fetched_at,
                country_hint=country_hint,
            )
        else:
            state.harvest.stats["non_html"] += 1
            return None
        return classify_page(page, self._page_types, is_home=path_of(raw.final_url) == "/")

    def _is_new_content(self, state: _CrawlState, page: ExtractedPage) -> bool:
        if page.page.content_hash in state.content_hashes:
            return False
        simhash = page.details.simhash
        title = (page.page.title or "").strip().casefold()
        if simhash is not None and len(page.page.text) > 200:
            # shared mega-menus make unrelated pages look alike, so a near-duplicate also needs the same title
            if any(
                hamming(simhash, seen) <= NEAR_DUPLICATE_BITS and seen_title == title
                for seen, seen_title in state.simhashes
            ):
                return False
            state.simhashes.append((simhash, title))
        state.content_hashes.add(page.page.content_hash)
        return True

    def _finish_inventory(self, state: _CrawlState) -> None:
        state.harvest.stats["discovered"] = len(state.candidates)
        ranked = sorted(state.candidates.values(), key=lambda candidate: (-candidate.score, candidate.url))
        state.harvest.inventory = [
            InventoryUrl(
                url=candidate.url,
                page_type=candidate.page_type,
                template_key=candidate.template_key,
                source=candidate.source,
                lastmod=candidate.lastmod,
            )
            for candidate in ranked[:MAX_INVENTORY]
        ]


def parent_path(url: str) -> str:
    path = path_of(url).rstrip("/")
    return path.rsplit("/", 1)[0] if path.count("/") > 1 else "/"


def classify_page(page: ExtractedPage, classifier: PageTypeClassifier, *, is_home: bool) -> ExtractedPage:
    json_ld_types = [str(block.get("@type")) for block in page.page.json_ld if block.get("@type")]
    page_type = classifier.classify(
        page.page.url,
        title=page.page.title or "",
        h1=page.page.h1 or "",
        json_ld_types=json_ld_types,
        is_home=is_home,
    )
    return page.model_copy(update={"page": page.page.model_copy(update={"page_type": page_type.value})})
