from typing import Any

from app.adapters.llm.testing import FakeLLMClient
from app.core.config import CrawlProfileSettings
from app.domain.web import BlockReason, CrawlBudget, SiteRef, UserAgentMode
from app.ports.llm import TaskId
from app.services.website_intel.crawl.crawler import PoliteCrawler, parent_path
from app.services.website_intel.crawl.politeness import HostGovernor
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.models import PageType, UrlCandidate
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.page_selector import LlmPageSelector
from tests.website_intel.conftest import CLIENT_ID, FakeRawFetcher, RecordingReporter, Served, html_page

ORIGIN = "https://www.clinic.com"
SITE = SiteRef(
    input_url="clinic.com",
    canonical_origin=ORIGIN,
    registrable_domain="clinic.com",
    scope_hosts=["clinic.com", "www.clinic.com"],
)
NAV = [
    ("/services/", "Our Services"),
    ("/about-us", "About us"),
    ("/contact-us", "Contact us"),
    ("/pricing", "Pricing"),
    ("/waf-page", "Special offers"),
    ("/private/admin", "Admin"),
]
ROBOTS = b"User-agent: *\nDisallow: /private/\nSitemap: https://www.clinic.com/sitemap.xml\n"
SITEMAP = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.clinic.com/services/implants</loc></url>
<url><loc>https://www.clinic.com/services/root-canal</loc></url>
<url><loc>https://www.clinic.com/private/secret</loc></url>
<url><loc>https://www.clinic.com/blog?page=9</loc></url>
<url><loc>https://other.com/elsewhere</loc></url>
</urlset>"""
WAF = (
    b"<html><head><title>403 Forbidden</title></head><body>"
    b"<center>Microsoft-Azure-Application-Gateway/v2</center></body></html>"
)


def body(topic: str) -> str:
    return (
        f"<p>{topic} " + " ".join(f"{topic} detail sentence number {index}." for index in range(60)) + "</p>"
    )


def site_pages() -> dict[str, Served]:
    links = [(ORIGIN + href, text) for href, text in NAV]
    pages = {
        f"{ORIGIN}/robots.txt": Served(200, ROBOTS, {"content-type": "text/plain"}, compat_only=True),
        f"{ORIGIN}/sitemap.xml": Served(200, SITEMAP, {"content-type": "application/xml"}, compat_only=True),
        f"{ORIGIN}/": Served(
            200, html_page("Smile Dental Clinic", body("home"), links=links), compat_only=True
        ),
        f"{ORIGIN}/waf-page": Served(403, WAF, {"content-type": "text/html"}),
    }
    for path, topic in [
        ("/services/", "services"),
        ("/about-us", "about"),
        ("/contact-us", "contact"),
        ("/pricing", "pricing"),
        ("/services/implants", "implants"),
        ("/services/root-canal", "rootcanal"),
    ]:
        pages[ORIGIN + path] = Served(
            200, html_page(topic.title(), body(topic), links=links), compat_only=True
        )
    return pages


def crawler(fetcher: FakeRawFetcher, clock: Any, **overrides: Any) -> PoliteCrawler:
    settings = CrawlProfileSettings(min_delay_s=0.0, fetch_budget=20, discovery_fetch_budget=4, **overrides)
    return PoliteCrawler(
        fetcher=fetcher,
        robots=RobotsService(fetcher, token="GrowthHackerBot"),
        governor=HostGovernor(max_concurrency=2, min_delay_s=0.0),
        extractor=PageExtractor(),
        settings=settings,
        clock=clock,
        **({"page_selector": overrides.pop("page_selector")} if "page_selector" in overrides else {}),
    )


async def test_crawl_switches_to_compat_agent_and_respects_robots(clock: Any) -> None:
    fetcher = FakeRawFetcher(site_pages())
    reporter = RecordingReporter()
    harvest = await crawler(fetcher, clock).harvest(SITE, CrawlBudget(), reporter)
    fetched = {page.page.url for page in harvest.pages}
    assert harvest.ua_mode is UserAgentMode.compat
    assert {
        f"{ORIGIN}/",
        f"{ORIGIN}/about-us",
        f"{ORIGIN}/contact-us",
        f"{ORIGIN}/services/implants",
    } <= fetched
    assert not any("/private/" in url for url, _ in fetcher.requests)
    assert all(mode is UserAgentMode.compat for url, mode in fetcher.requests if url.endswith("/about-us"))
    assert [(attempt.url, attempt.reason) for attempt in harvest.blocked] == [
        (f"{ORIGIN}/waf-page", BlockReason.waf_block)
    ]
    assert harvest.stats["robots_disallowed"] >= 1 and harvest.stats["traps"] >= 1
    assert harvest.stats["sitemap_off_host"] == 1
    assert "crawl.blocked" in reporter.names()
    page_types = {page.page.url: page.page.page_type for page in harvest.pages}
    assert page_types[f"{ORIGIN}/"] == PageType.home.value
    assert page_types[f"{ORIGIN}/contact-us"] == PageType.contact.value


async def test_robots_disallow_all_stops_the_crawl(clock: Any) -> None:
    pages = site_pages()
    pages[f"{ORIGIN}/robots.txt"] = Served(
        200, b"User-agent: *\nDisallow: /\n", {"content-type": "text/plain"}
    )
    reporter = RecordingReporter()
    harvest = await crawler(FakeRawFetcher(pages), clock).harvest(SITE, CrawlBudget(), reporter)
    assert harvest.pages == [] and "robots_disallow_all" in harvest.notes


async def test_robots_waf_for_both_agents_means_disallow_all(clock: Any) -> None:
    pages = site_pages()
    pages[f"{ORIGIN}/robots.txt"] = Served(403, WAF, {"content-type": "text/html"})
    harvest = await crawler(FakeRawFetcher(pages), clock).harvest(SITE, CrawlBudget(), RecordingReporter())
    assert harvest.pages == []
    assert harvest.robots is not None and harvest.robots.status.value == "disallow_all"


async def test_llm_selector_ids_are_mapped_and_topped_up(clock: Any) -> None:
    llm = FakeLLMClient({TaskId.website_page_selection: {"picks": [{"id": "U1"}, {"id": "U999"}]}})
    selector = LlmPageSelector(LlmGateway(llm, client_id=CLIENT_ID, run_id=None))
    candidates = [
        UrlCandidate(url=f"{ORIGIN}/a", source="sitemap", page_type=PageType.offering_detail),
        UrlCandidate(url=f"{ORIGIN}/b", source="sitemap", page_type=PageType.blog_post),
    ]
    picked = await selector.select(
        candidates=candidates, mandatory=[], budget=5, nav=[], templates=[], business_name="Clinic"
    )
    assert picked == [f"{ORIGIN}/a"]
    [call] = llm.calls_for(TaskId.website_page_selection)
    assert any(block.label == "candidates" for block in call.input.untrusted)


async def test_crawl_uses_selector_and_survives_its_failure(clock: Any) -> None:
    from app.ports.llm import LLMError, LLMErrorKind

    llm = FakeLLMClient({TaskId.website_page_selection: LLMError(LLMErrorKind.overloaded, "busy")})
    selector = LlmPageSelector(LlmGateway(llm, client_id=CLIENT_ID, run_id=None))
    harvest = await crawler(FakeRawFetcher(site_pages()), clock, page_selector=selector).harvest(
        SITE, CrawlBudget(), RecordingReporter()
    )
    assert len(harvest.pages) >= 5


async def test_host_governor_backs_off_and_stops_after_three_throttles() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    governor = HostGovernor(max_concurrency=2, min_delay_s=1.0, sleep=sleep, monotonic=lambda: 0.0)
    assert governor.record("a.com", 429, retry_after_s=500) == 120.0
    assert governor.delay_for("a.com") == 2.0
    assert governor.record("a.com", 200) is None
    governor.record("a.com", 503)
    governor.record("a.com", 503)
    assert governor.record("a.com", 503) is None and governor.stopped("a.com")
    async with governor.slot("b.com"):
        pass
    async with governor.slot("b.com"):
        pass
    assert slept == [1.0]


def test_parent_path() -> None:
    assert parent_path("https://a.com/hospitals/apollo-indore") == "/hospitals"
    assert parent_path("https://a.com/about") == "/"
