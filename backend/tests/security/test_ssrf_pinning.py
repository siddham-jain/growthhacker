import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import respx

from app.adapters.crawler.http_fetcher import DirectHttpFetcher
from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.rss import MAX_FEED_BYTES, RssSource
from app.core.clock import FrozenClock
from app.ports.crawler import FetchContext
from app.services.refresh.links.checker import ROBOTS_MAX_BYTES, LinkChecker
from app.services.refresh.links.status import LinkStatusClass
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.inputs.ssrf import PinnedTransport, SsrfGuard, pinned
from tests.conftest import make_settings
from tests.news.adapters.conftest import NOW, SINCE, planned

AGENTS = UserAgents(honest="ua", compat="ua")
CTX = FetchContext(client_id=None, run_id=None)
CLOCK = FrozenClock(NOW)


class Echo(BaseHTTPRequestHandler):
    hosts: list[str] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.hosts.append(self.headers["host"])
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>pinned</body></html>")


@pytest.fixture
def server() -> Iterator[int]:
    Echo.hosts = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


async def test_pinned_transport_connects_to_the_vetted_address_and_keeps_the_host(server: int) -> None:
    url = f"http://rebind.invalid:{server}/"
    async with httpx.AsyncClient(transport=PinnedTransport()) as client:
        with pinned(url, ["127.0.0.1"]):
            response = await client.get(url)
        with pytest.raises(httpx.ConnectError):
            # outside the pin the name is resolved again, which is exactly what rebinding relies on
            await client.get(url)
    assert response.status_code == 200
    assert Echo.hosts == [f"rebind.invalid:{server}"]


async def test_direct_fetcher_uses_the_address_its_guard_vetted(server: int) -> None:
    class LocalGuard(SsrfGuard):
        async def check(self, url: str) -> list[str]:
            return ["127.0.0.1"]

    async with httpx.AsyncClient(transport=PinnedTransport()) as client:
        fetcher = DirectHttpFetcher(client, user_agents=AGENTS, clock=CLOCK, guard=LocalGuard())
        result = await fetcher.fetch(f"http://site.invalid:{server}/", CTX)
    assert result.html is not None and "pinned" in result.html
    assert Echo.hosts == [f"site.invalid:{server}"]


async def public(host: str, port: int) -> list[str]:
    return ["10.0.0.7"] if host == "internal.example" else ["93.184.216.34"]


@respx.mock
async def test_pdf_cap_needs_a_real_pdf_not_just_the_header() -> None:
    respx.get("https://example.com/fake.pdf").mock(
        return_value=httpx.Response(
            200, content=b"<html>" + b"a" * 5000, headers={"content-type": "application/pdf"}
        )
    )
    async with httpx.AsyncClient() as client:
        fetcher = DirectHttpFetcher(
            client, user_agents=AGENTS, clock=CLOCK, guard=SsrfGuard(public), max_pdf_bytes=1_000_000
        )
        raw = await fetcher.fetch_raw(
            "https://example.com/fake.pdf", CTX.model_copy(update={"max_bytes": 1000})
        )
    assert raw.too_large


@pytest.fixture
def feed_deps() -> FreshnessDeps:
    return FreshnessDeps(http=httpx.AsyncClient(), settings=make_settings(), clock=CLOCK)


@respx.mock
async def test_rss_refuses_private_feeds_and_private_redirects(feed_deps: FreshnessDeps) -> None:
    source = RssSource(feed_deps, guard=SsrfGuard(public))
    direct = await source.fetch(planned("rss", "a", url="http://internal.example/feed"), SINCE)
    assert direct.status == "error" and "public" in (direct.error or "")
    respx.get("https://news.example.com/feed").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal.example/latest/meta-data"})
    )
    hop = await source.fetch(planned("rss", "b", url="https://news.example.com/feed"), SINCE)
    assert hop.status == "error" and "public" in (hop.error or "")


@respx.mock
async def test_rss_stops_reading_an_oversized_feed(feed_deps: FreshnessDeps) -> None:
    respx.get("https://news.example.com/huge").mock(
        return_value=httpx.Response(200, content=b"<rss>" + b" " * (MAX_FEED_BYTES + 10))
    )
    result = await RssSource(feed_deps, guard=SsrfGuard(public)).fetch(
        planned("rss", "huge", url="https://news.example.com/huge"), SINCE
    )
    assert result.status == "error" and "bytes" in (result.error or "")


async def link_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def checker(client: httpx.AsyncClient) -> LinkChecker:
    async def no_sleep(_: float) -> None:
        return None

    return LinkChecker(client, make_settings().refresh, CLOCK, resolver=link_resolver, sleep=no_sleep)


@respx.mock
async def test_link_checker_only_probes_web_ports() -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        status = await checker(client).probe("https://example.com:6379/")
    assert status.status_class is LinkStatusClass.unverifiable and "port" in (status.error or "")
    assert not route.called


@respx.mock
async def test_link_checker_reads_only_the_head_of_a_huge_robots_file() -> None:
    allow_then_junk = b"User-agent: *\nAllow: /\n" + b"#" * (ROBOTS_MAX_BYTES * 4)
    robots = respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, content=allow_then_junk)
    )
    respx.head("https://example.com/page").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        status = await checker(client).probe("https://example.com/page")
    assert robots.called and status.status_class is LinkStatusClass.ok
