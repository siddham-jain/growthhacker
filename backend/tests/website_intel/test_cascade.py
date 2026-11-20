from datetime import timedelta
from typing import Any

from app.adapters.context.archive import ArchiveSource
from app.adapters.context.knowledge_base import KnowledgeBaseSource, knowledge_text
from app.adapters.context.reader_proxy import ReaderProxySource
from app.adapters.context.search_index import SearchIndexSource
from app.adapters.context.site_direct import SiteDirectSource
from app.adapters.context.site_rendered import SiteRenderedSource
from app.adapters.context.user_material import UserMaterialSource
from app.adapters.context.web_mentions import WebMentionsSource
from app.adapters.context.youtube_channel import YouTubeChannelSource
from app.core.config import CrawlProfileSettings, WebsiteIntelSettings
from app.core.registry import Registry
from app.domain.runs import SourceFallbackPayload
from app.domain.web import BlockReason, CoverageFacet, CrawlBudget, FetchOutcome, FetchResult, SourceKind
from app.domain.youtube import ChannelRecord
from app.ports.context import BlockedAttempt
from app.ports.search import (
    ExtractedDocument,
    ExtractResponse,
    SiteMapResponse,
    WebSearchHit,
    WebSearchQuery,
    WebSearchResponse,
)
from app.ports.youtube import VideoBatch
from app.services.website_intel.acquisition.cascade import AcquisitionCascade
from app.services.website_intel.acquisition.coverage import CoverageScorer
from app.services.website_intel.acquisition.models import (
    CascadeRequest,
    RobotsSnapshot,
    UserMaterial,
    harvest,
)
from app.services.website_intel.acquisition.ports import ArchivedPage, ReaderDocument
from app.services.website_intel.acquisition.toolkit import ContextToolkit, build_sources
from app.services.website_intel.crawl.crawler import PoliteCrawler
from app.services.website_intel.crawl.politeness import HostGovernor
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.identity.knowledge import KbEntity, KbRelated, KbSummary
from app.services.website_intel.models import PageVerdict
from tests.website_intel.conftest import (
    APOLLO,
    CLIENT_ID,
    NOW,
    FakeRawFetcher,
    RecordingReporter,
    Served,
    cta,
    make_page,
)

HOME = "https://www.apollohospitals.com/"
WAF_403 = (
    b"<html><head><title>403 Forbidden</title></head><body><center>Microsoft-Azure-Application-Gateway/v2"
    b"</center></body></html>"
)
HOME_MARKDOWN = (
    "# Apollo Hospitals\n\nApollo Hospitals offers cardiology, oncology and orthopaedics across India. "
    + "Book a health check today. " * 20
    + "\n\n[Book Appointment](https://www.apollohospitals.com/book-doctor-appointment/)"
    + "\n[Call 1066](tel:1066)\n[Contact Us](https://www.apollohospitals.com/contact-us)"
)


def request(**changes: Any) -> CascadeRequest:
    values: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "run_id": None,
        "business_name": "Apollo Hospitals",
        "site": APOLLO,
        "channel_id": None,
        "missing_facets": frozenset(CoverageFacet),
        "country_hint": "IN",
    }
    return CascadeRequest(**{**values, **changes})


def toolkit(clock: Any, **changes: Any) -> ContextToolkit:
    return ContextToolkit(
        settings=WebsiteIntelSettings(),
        profile=CrawlProfileSettings(min_delay_s=0.0, discovery_fetch_budget=3, fetch_budget=6),
        clock=clock,
        extractor=PageExtractor(),
        **changes,
    )


class StubSource:
    def __init__(
        self,
        kind: SourceKind,
        tier: int,
        pages: list[Any],
        *,
        blocked: list[BlockedAttempt] = (),
        always: bool = False,
        credits: float = 0,
    ) -> None:  # type: ignore[assignment]
        self.source_kind = kind
        self.tier = tier
        self._pages = pages
        self._blocked = list(blocked)
        self._always = always
        self._credits = credits
        self.ran = False

    def applicable(self, request: CascadeRequest) -> bool:
        return self._always or bool(request.missing_facets)

    async def acquire(self, request: CascadeRequest, budget: CrawlBudget, reporter: Any) -> Any:
        self.ran = True
        return harvest(self.source_kind, self._pages, blocked=self._blocked, tavily_credits=self._credits)


async def test_cascade_routes_around_a_blocked_site_and_stops_when_covered(
    reporter: RecordingReporter,
) -> None:
    blocked = [BlockedAttempt(url=HOME, source_kind=SourceKind.site_direct, reason=BlockReason.waf_block)]
    rich_home = make_page(
        HOME,
        "x" * 2000,
        source=SourceKind.reader_proxy,
        page_type="home",
        ctas=[cta("book_appointment", HOME + "book", HOME), cta("call", "tel:1066", HOME)],
    )
    offerings = make_page(
        HOME + "departments", "cardiology", source=SourceKind.reader_proxy, page_type="offering_hub"
    )
    direct = StubSource(SourceKind.site_direct, 1, [], blocked=blocked)
    reader = StubSource(SourceKind.reader_proxy, 3, [rich_home, offerings], credits=2)
    search = StubSource(SourceKind.search_index, 4, [])
    knowledge = StubSource(
        SourceKind.knowledge_base,
        6,
        [make_page("https://en.wikipedia.org/wiki/Apollo", "kb", source=SourceKind.knowledge_base)],
        always=True,
    )
    outcome = await AcquisitionCascade([search, knowledge, reader, direct]).run(
        request(), CrawlBudget(), reporter
    )
    assert direct.ran and reader.ran and knowledge.ran and not search.ran
    assert outcome.missing == frozenset()
    assert outcome.tavily_credits == 2
    assert [tier.tier for tier in outcome.tiers] == [1, 3, 6]
    names = reporter.names()
    assert names.count("crawl.source_started") == 3
    assert "crawl.blocked" in names and "crawl.fallback" in names
    fallback = next(payload for payload in reporter.payloads if isinstance(payload, SourceFallbackPayload))
    assert (fallback.from_source, fallback.to_source) == ("site_direct", "reader_proxy")


async def test_cascade_skips_credit_tiers_once_the_budget_is_spent(reporter: RecordingReporter) -> None:
    spender = StubSource(SourceKind.reader_proxy, 3, [], credits=5)
    search = StubSource(SourceKind.search_index, 4, [])
    outcome = await AcquisitionCascade([spender, search]).run(
        request(), CrawlBudget(max_tavily_credits=5), reporter
    )
    assert not search.ran
    assert any("credit budget spent" in note for note in outcome.notes)


def test_build_sources_honours_disabled_tiers_and_missing_collaborators(clock: Any) -> None:
    registry: Registry[Any] = Registry("test_sources")
    registry.add("site_direct", lambda kit: StubSource(SourceKind.site_direct, 1, []))
    registry.add("knowledge_base", lambda kit: None)
    registry.add("archive", lambda kit: StubSource(SourceKind.archive, 5, []))
    kit = toolkit(clock)
    kit.settings = WebsiteIntelSettings(disabled_sources=["archive"])
    assert [source.source_kind for source in build_sources(kit, registry)] == [SourceKind.site_direct]


def test_coverage_scorer_facets() -> None:
    scorer = CoverageScorer()
    assert scorer.page_facets(make_page(HOME, "short", page_type="home")) == {CoverageFacet.home}
    contact = make_page(HOME + "contact", "x", page_type="contact", ctas=[cta("call", "tel:1", HOME)])
    assert scorer.page_facets(contact) == {CoverageFacet.contact_locations, CoverageFacet.conversion_actions}
    kb = make_page("https://en.wikipedia.org/wiki/X", "x", source=SourceKind.knowledge_base)
    assert scorer.page_facets(kb) == {CoverageFacet.about}


class FakeSearch:
    def __init__(self, *, extract_body: str = HOME_MARKDOWN, hits: list[WebSearchHit] | None = None) -> None:
        self.extract_body = extract_body
        self.hits = hits or []
        self.queries: list[WebSearchQuery] = []
        self.extracted: list[list[str]] = []
        self.mapped = 0

    async def search(self, query: WebSearchQuery) -> WebSearchResponse:
        self.queries.append(query)
        return WebSearchResponse(
            query=query, hits=self.hits, credits_used=1, from_cache=False, fetched_at=NOW
        )

    async def extract(self, request: Any) -> ExtractResponse:
        self.extracted.append(list(request.urls))
        return ExtractResponse(
            results=[ExtractedDocument(url=request.urls[0], raw_content=self.extract_body)],
            credits_used=2,
            fetched_at=NOW,
        )

    async def map(self, request: Any) -> SiteMapResponse:
        self.mapped += 1
        return SiteMapResponse(
            base_url=request.url,
            urls=[HOME + "about-us", "https://elsewhere.com/x"],
            credits_used=1,
            fetched_at=NOW,
        )

    async def crawl(self, request: Any) -> ExtractResponse:
        return ExtractResponse(results=[], credits_used=0, fetched_at=NOW)


class FakeReader:
    name = "fake_reader"

    def __init__(self, documents: dict[str, ReaderDocument]) -> None:
        self.documents = documents
        self.reads: list[str] = []

    async def read(self, url: str) -> ReaderDocument:
        self.reads.append(url)
        return self.documents.get(url) or ReaderDocument(url, url, None, "", PageVerdict.error, 422)


async def test_reader_proxy_treats_waf_copy_as_blocked_then_uses_reader(clock: Any) -> None:
    jina_waf = "\"Title: Azure WAF\" One moment, we're checking you're not a robot appgwazwaf"
    search = FakeSearch(extract_body=jina_waf)
    reader = FakeReader(
        {HOME: ReaderDocument(HOME, HOME, "Apollo Hospitals", HOME_MARKDOWN, PageVerdict.ok, 200)}
    )
    source = ReaderProxySource(search, [reader], toolkit(clock))
    result = await source.acquire(request(), CrawlBudget(), RecordingReporter())
    assert search.extracted and reader.reads[0] == HOME
    assert [page.page.source_kind for page in result.extracted] == [SourceKind.reader_proxy]
    assert result.extracted[0].details.ctas
    assert any(attempt.reason is BlockReason.waf_block for attempt in result.blocked)
    assert HOME + "contact-us" in search.extracted[1]
    assert result.tavily_credits == 4


async def test_reader_proxy_skips_urls_our_robots_disallow(clock: Any) -> None:
    robots = RobotsSnapshot(status_code=200, body="User-agent: *\nDisallow: /\n", token="GrowthHackerBot")
    reader = FakeReader({})
    result = await ReaderProxySource(None, [reader], toolkit(clock)).acquire(
        request(robots=robots), CrawlBudget(), RecordingReporter()
    )
    assert reader.reads == [] and result.extracted == []


async def test_search_index_keeps_on_domain_bodies_and_maps_inventory(clock: Any) -> None:
    hits = [
        WebSearchHit(
            url=HOME + "specialities",
            title="Specialities",
            snippet="",
            raw_content="# Specialities\n" + "Cardiology care. " * 30,
        ),
        WebSearchHit(url="https://news.example.com/apollo", title="News", snippet="", raw_content="x" * 500),
        WebSearchHit(url=HOME + "short", title="Short", snippet="", raw_content="tiny"),
    ]
    search = FakeSearch(hits=hits)
    result = await SearchIndexSource(search, toolkit(clock)).acquire(
        request(missing_facets=frozenset({CoverageFacet.offerings, CoverageFacet.about})),
        CrawlBudget(),
        RecordingReporter(),
    )
    assert [page.page.url for page in result.extracted] == [HOME + "specialities"]
    assert all(
        query.include_domains == ["apollohospitals.com"] and query.include_raw_content
        for query in search.queries
    )
    assert search.mapped == 1 and [item.url for item in result.inventory] == [HOME + "about-us"]
    assert result.tavily_credits == len(search.queries) + 1


class FakeArchive:
    name = "fake_archive"

    def __init__(self, age_days: int) -> None:
        self.age_days = age_days

    async def latest(self, url: str) -> ArchivedPage | None:
        html = f"<html><head><title>Apollo</title></head><body><main><p>{'Archived cardiology care. ' * 40}</p></main></body></html>"
        return ArchivedPage(
            url, "https://web.archive.org/x", NOW - timedelta(days=self.age_days), 200, "text/html", html
        )


async def test_archive_pages_carry_snapshot_and_stale_note(clock: Any) -> None:
    result = await ArchiveSource([FakeArchive(700)], toolkit(clock)).acquire(
        request(), CrawlBudget(), RecordingReporter()
    )
    page = result.extracted[0].page
    assert page.source_kind is SourceKind.archive and page.snapshot_at is not None
    assert any("flagged stale" in note for note in result.notes)


class FakeKnowledge:
    async def search(self, name: str, *, language: str = "en", limit: int = 5) -> list[KbEntity]:
        return [
            KbEntity(
                qid="Q4780321",
                label="Apollo Hospitals",
                description="chain of Indian private hospitals",
                websites=["http://www.apollohospitals.com/"],
                industries=["healthcare industry"],
                headquarters=["Chennai"],
                subsidiaries=[KbRelated(name="Apollo Pharmacy", website="https://www.apollopharmacy.in/")],
                wikipedia_title="Apollo Hospitals",
            ),
            KbEntity(qid="Q2", label="Apollo Tyres", websites=["https://apollotyres.com"]),
        ]

    async def summary(self, title: str, *, language: str = "en") -> KbSummary | None:
        return KbSummary(
            title=title,
            extract="Apollo Hospitals is an Indian hospital chain.",
            url="https://en.wikipedia.org/wiki/Apollo_Hospitals",
        )


async def test_knowledge_base_matches_by_domain(clock: Any) -> None:
    result = await KnowledgeBaseSource(FakeKnowledge(), toolkit(clock)).acquire(
        request(), CrawlBudget(), RecordingReporter()
    )
    assert result.knowledge_entity is not None and result.knowledge_entity.qid == "Q4780321"
    page = result.extracted[0].page
    assert page.source_kind is SourceKind.knowledge_base and "Subsidiaries: Apollo Pharmacy" in page.text


def test_knowledge_text_has_one_fact_per_line() -> None:
    entity = KbEntity(qid="Q1", label="Clinic", industries=["dentistry"], inception_year=1999)
    assert knowledge_text(entity, None).splitlines() == ["Clinic", "Industry: dentistry", "Founded: 1999"]


async def test_web_mentions_exclude_own_domain_and_drop_third_party_ctas(clock: Any) -> None:
    hits = [
        WebSearchHit(
            url="https://www.practo.com/apollo",
            title="Apollo on Practo",
            snippet="",
            raw_content="Apollo Hospitals [Book](https://www.practo.com/book) cardiology",
        )
    ]
    search = FakeSearch(hits=hits)
    result = await WebMentionsSource(search, toolkit(clock)).acquire(
        request(), CrawlBudget(), RecordingReporter()
    )
    assert search.queries[0].exclude_domains == ["apollohospitals.com"]
    page = result.extracted[0]
    assert page.page.source_kind is SourceKind.web_mentions and page.details.ctas == []


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def channels_by_id(self, ids: Any, ctx: Any) -> list[ChannelRecord]:
        self.calls.append("channels")
        return [
            ChannelRecord(
                channel_id="UC0InVdvqNyNzKBl1-TL348A",
                title="Apollo Hospitals",
                handle="@apollohospitalsindia",
                description="Book now https://wa.me/919999999999 and https://www.apollohospitals.com/",
                country="IN",
                default_language="en",
                published_at=NOW,
                uploads_playlist_id=None,
                subscriber_count=100,
                hidden_subscriber_count=False,
                view_count=1,
                video_count=10,
                keywords=["best cardiology hospital", "best hospital chennai"],
                thumbnail_url=None,
                metadata_fetched_at=NOW,
                stats_fetched_at=NOW,
            )
        ]

    async def videos_by_id(self, ids: Any, ctx: Any) -> VideoBatch:
        return VideoBatch(found=[])


async def test_youtube_channel_source_reads_snippet_through_gateway(clock: Any) -> None:
    gateway = FakeGateway()
    result = await YouTubeChannelSource(gateway, toolkit(clock)).acquire(
        request(channel_id="UC0InVdvqNyNzKBl1-TL348A"), CrawlBudget(), RecordingReporter()
    )
    page = result.extracted[0]
    assert "Channel keywords: best cardiology hospital" in page.page.text
    assert any(item.type == "whatsapp" for item in page.details.ctas)


async def test_user_material_becomes_user_pages(clock: Any) -> None:
    material = [
        UserMaterial(kind="text", content="We run 3 dental clinics in Pune offering implants."),
        UserMaterial(kind="url_list", content="https://c.com/a\nhttps://c.com/b"),
    ]
    result = await UserMaterialSource(toolkit(clock)).acquire(
        request(user_material=material), CrawlBudget(), RecordingReporter()
    )
    assert [page.page.source_kind for page in result.extracted] == [SourceKind.user]
    assert result.remaining_urls == ["https://c.com/a", "https://c.com/b"]


class FakeRenderer:
    name = "render"

    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.rendered: list[str] = []

    async def render(self, url: str, ctx: Any) -> FetchResult:
        self.rendered.append(url)
        if not self.ok:
            return FetchResult(
                requested_url=url,
                final_url=url,
                source_kind=SourceKind.site_rendered,
                outcome=FetchOutcome.blocked,
                status_code=403,
                block_reason=BlockReason.waf_block,
                fetched_at=NOW,
            )
        html = f"<html><head><title>Apollo</title></head><body><main><p>{'Rendered cardiology content. ' * 40}</p></main></body></html>"
        return FetchResult(
            requested_url=url,
            final_url=url,
            source_kind=SourceKind.site_rendered,
            outcome=FetchOutcome.ok,
            status_code=200,
            html=html,
            fetched_at=NOW,
        )


async def test_rendered_source_retries_blocked_urls_in_a_browser(clock: Any) -> None:
    blocked = [BlockedAttempt(url=HOME, source_kind=SourceKind.site_direct, reason=BlockReason.waf_block)]
    renderer = FakeRenderer(ok=True)
    robots = RobotsSnapshot(status_code=200, body="User-agent: *\nAllow: /\n", token="GrowthHackerBot")
    source = SiteRenderedSource(renderer, None, toolkit(clock))
    req = request(prior_blocked=blocked, robots=robots)
    assert source.applicable(req)
    result = await source.acquire(req, CrawlBudget(), RecordingReporter())
    assert renderer.rendered[0] == HOME
    assert result.extracted[0].page.source_kind is SourceKind.site_rendered


class Always403Fetcher(FakeRawFetcher):
    def __init__(self) -> None:
        super().__init__({})

    async def fetch_raw(
        self, url: str, ctx: Any, *, etag: str | None = None, last_modified: str | None = None
    ) -> Any:
        self.pages[url] = Served(403, WAF_403, {"content-type": "text/html"})
        return await super().fetch_raw(url, ctx)


async def test_forced_site_failure_still_yields_context_from_later_tiers(
    clock: Any, reporter: RecordingReporter
) -> None:
    fetcher = Always403Fetcher()
    kit = toolkit(clock)
    crawler = PoliteCrawler(
        fetcher=fetcher,
        robots=RobotsService(fetcher, token="GrowthHackerBot"),
        governor=HostGovernor(max_concurrency=1, min_delay_s=0.0),
        extractor=kit.extractor,
        settings=kit.profile,
        clock=clock,
    )
    reader = FakeReader(
        {HOME: ReaderDocument(HOME, HOME, "Apollo Hospitals", HOME_MARKDOWN, PageVerdict.ok, 200)}
    )
    sources = [
        SiteDirectSource(crawler, kit),
        SiteRenderedSource(FakeRenderer(ok=False), None, kit),
        ReaderProxySource(None, [reader], kit),
        KnowledgeBaseSource(FakeKnowledge(), kit),
    ]
    outcome = await AcquisitionCascade(sources).run(request(), CrawlBudget(), reporter)
    kinds = {page.page.source_kind for page in outcome.pages}
    assert SourceKind.site_direct not in kinds and SourceKind.reader_proxy in kinds
    assert {CoverageFacet.home, CoverageFacet.conversion_actions, CoverageFacet.about} <= outcome.covered
    assert "crawl.fallback" in reporter.names()
