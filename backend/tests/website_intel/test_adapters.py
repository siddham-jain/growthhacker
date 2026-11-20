import gzip
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from tavily.errors import UsageLimitExceededError

from app.adapters.context.wikidata import WikidataKnowledgeBase
from app.adapters.crawler.archive import WaybackArchive, parse_warc_response
from app.adapters.crawler.http_fetcher import DirectHttpFetcher
from app.adapters.crawler.reader_proxy import JinaReader
from app.adapters.search.cache import cache_key
from app.adapters.search.errors import WebSearchError
from app.adapters.search.ledger import CreditCharge
from app.adapters.search.tavily import TavilyWebSearch
from app.domain.web import FetchOutcome, UserAgentMode
from app.ports.crawler import FetchContext
from app.ports.search import ExtractRequest, SiteMapRequest, WebSearchQuery
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.inputs.ssrf import SsrfGuard, UnsafeTargetError
from app.services.website_intel.models import PageVerdict

FIXTURES = Path(__file__).parent / "fixtures"
AGENTS = UserAgents(
    honest="Mozilla/5.0 (compatible; GrowthHackerBot/1.0)", compat="Chrome/131 GrowthHackerBot/1.0"
)


async def public_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"] if host == "internal.example" else ["93.184.216.34"]


def fetcher(client: httpx.AsyncClient, clock: Any) -> DirectHttpFetcher:
    return DirectHttpFetcher(client, user_agents=AGENTS, clock=clock, guard=SsrfGuard(public_resolver))


@respx.mock
async def test_fetcher_follows_redirects_and_classifies_azure_waf(clock: Any) -> None:
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.example.com/"})
    )
    waf = (FIXTURES / "crawl_block_azure_403.html").read_bytes()
    respx.get("https://www.example.com/").mock(
        return_value=httpx.Response(403, content=waf, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as client:
        raw = await fetcher(client, clock).fetch_raw(
            "https://example.com/", FetchContext(client_id=None, run_id=None)
        )
    assert raw.redirect_chain == ["https://example.com/"]
    assert raw.final_url == "https://www.example.com/"
    assert raw.verdict is PageVerdict.waf_block


@respx.mock
async def test_fetcher_sends_the_mode_user_agent_and_rejects_private_redirects(clock: Any) -> None:
    route = respx.get("https://example.com/").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal.example/admin"})
    )
    ctx = FetchContext(client_id=None, run_id=None, ua_mode=UserAgentMode.compat)
    async with httpx.AsyncClient() as client:
        with pytest.raises(UnsafeTargetError):
            await fetcher(client, clock).fetch_raw("https://example.com/", ctx)
    assert route.calls[0].request.headers["user-agent"] == AGENTS.compat


@respx.mock
async def test_fetcher_caps_body_size_and_reports_too_large(clock: Any) -> None:
    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200, content=b"<html>" + b"a" * 5000, headers={"content-type": "text/html"}
        )
    )
    ctx = FetchContext(client_id=None, run_id=None, max_bytes=1000)
    async with httpx.AsyncClient() as client:
        result = await fetcher(client, clock).fetch("https://example.com/big", ctx)
    assert result.outcome is FetchOutcome.too_large


@respx.mock
async def test_fetcher_decodes_declared_charset(clock: Any) -> None:
    body = "<html><head><meta charset='windows-1252'></head><body>café</body></html>".encode("windows-1252")
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as client:
        result = await fetcher(client, clock).fetch(
            "https://example.com/", FetchContext(client_id=None, run_id=None)
        )
    assert result.html is not None and "café" in result.html


@respx.mock
async def test_jina_reader_treats_wrapped_waf_page_as_blocked() -> None:
    payload = json.loads((FIXTURES / "crawl_block_jina_azure_waf.json").read_text())
    respx.get(url__startswith="https://r.jina.ai/").mock(return_value=httpx.Response(200, json=payload))
    async with httpx.AsyncClient() as client:
        document = await JinaReader(client, user_agent="bot").read("https://www.apollohospitals.com/")
    assert document.verdict is PageVerdict.waf_block


@respx.mock
async def test_jina_reader_returns_markdown_when_ok() -> None:
    content = "# Clinic\n\n" + "We offer dental implants and root canal treatment. " * 20
    respx.get(url__startswith="https://r.jina.ai/").mock(
        return_value=httpx.Response(
            200, json={"code": 200, "data": {"title": "Clinic", "url": "https://c.com/", "content": content}}
        )
    )
    async with httpx.AsyncClient() as client:
        document = await JinaReader(client, user_agent="bot").read("https://c.com/")
    assert document.verdict is PageVerdict.ok
    assert document.markdown.startswith("# Clinic")


@respx.mock
async def test_wayback_picks_latest_capture_via_raw_url() -> None:
    respx.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(
            200,
            json=[
                ["timestamp", "original", "mimetype"],
                ["20261026103254", "https://www.apollohospitals.com/", "text/html"],
            ],
        )
    )
    raw = respx.get("https://web.archive.org/web/20261026103254id_/https://www.apollohospitals.com/").mock(
        return_value=httpx.Response(
            200, content=b"<html><body>archived</body></html>", headers={"content-type": "text/html"}
        )
    )
    async with httpx.AsyncClient() as client:
        archive = WaybackArchive(
            client,
            cdx_url="https://web.archive.org/cdx/search/cdx",
            raw_url_template="https://web.archive.org/web/{timestamp}id_/{url}",
            user_agent="bot",
        )
        page = await archive.latest("https://www.apollohospitals.com/")
    assert raw.called
    assert page is not None and page.snapshot_at.isoformat() == "2026-10-26T10:32:54+00:00"
    assert "archived" in page.html


def test_parse_warc_response_splits_record() -> None:
    record = (
        b"WARC/1.0\r\nWARC-Type: response\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html>hello</html>"
    )
    status, content_type, body = parse_warc_response(gzip.compress(record))  # type: ignore[misc]
    assert (status, content_type, body) == (200, "text/html", b"<html>hello</html>")
    assert parse_warc_response(b"not gzip") is None


@respx.mock
async def test_wikidata_search_resolves_linked_labels() -> None:
    api = "https://www.wikidata.org/w/api.php"

    def responder(request: httpx.Request) -> httpx.Response:
        action = request.url.params["action"]
        if action == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q4780321"}]})
        ids = request.url.params["ids"]
        if ids == "Q4780321":
            claims = {
                "P856": [{"mainsnak": {"datavalue": {"value": "http://www.apollohospitals.com/"}}}],
                "P452": [{"mainsnak": {"datavalue": {"value": {"id": "Q31207"}}}}],
                "P355": [{"mainsnak": {"datavalue": {"value": {"id": "Q62562612"}}}}],
                "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1983-00-00T00:00:00Z"}}}}],
            }
            entity = {
                "id": "Q4780321",
                "labels": {"en": {"value": "Apollo Hospitals"}},
                "descriptions": {"en": {"value": "chain of Indian private hospitals"}},
                "claims": claims,
                "sitelinks": {"enwiki": {"title": "Apollo Hospitals"}},
            }
            return httpx.Response(200, json={"entities": {"Q4780321": entity}})
        linked = {
            "Q31207": {"id": "Q31207", "labels": {"en": {"value": "healthcare industry"}}, "claims": {}},
            "Q62562612": {
                "id": "Q62562612",
                "labels": {"en": {"value": "Apollo Pharmacy"}},
                "claims": {
                    "P856": [{"mainsnak": {"datavalue": {"value": "https://www.apollopharmacy.in/"}}}]
                },
            },
        }
        return httpx.Response(200, json={"entities": linked})

    respx.get(api).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        kb = WikidataKnowledgeBase(
            client, api_url=api, summary_url="https://{lang}.wikipedia.org/{title}", user_agent="bot"
        )
        [entity] = await kb.search("Apollo Hospitals")
    assert entity.domains == ["apollohospitals.com"]
    assert entity.industries == ["healthcare industry"]
    assert entity.subsidiaries[0].name == "Apollo Pharmacy"
    assert entity.inception_year == 1983
    assert entity.wikipedia_title == "Apollo Hospitals"


class FakeTavily:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: Exception | None = None

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search", {"query": query, **kwargs}))
        if self.fail:
            raise self.fail
        return {
            "results": [
                {
                    "url": "https://www.apollohospitals.com/about",
                    "title": "About",
                    "content": "snippet",
                    "raw_content": "# About",
                }
            ],
            "usage": {"credits": 1},
        }

    async def extract(self, urls: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("extract", {"urls": urls, **kwargs}))
        return {
            "results": [{"url": urls[0], "raw_content": "# Home"}],
            "failed_results": [{"url": url, "error": "Failed to fetch url"} for url in urls[1:]],
        }

    async def map(self, url: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("map", {"url": url, **kwargs}))
        return {
            "base_url": url,
            "results": [f"{url}/p{index}" for index in range(15)],
            "usage": {"credits": 2},
        }

    async def crawl(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return {"results": [], "usage": {"credits": 0}}


class DictCache:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self.values.get(key)

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        self.values[key] = value


class ListRecorder:
    def __init__(self) -> None:
        self.charges: list[CreditCharge] = []

    async def record(self, charge: CreditCharge, *, client_id: Any, run_id: Any) -> None:
        self.charges.append(charge)


async def test_tavily_search_maps_raw_content_and_uses_cache(clock: Any) -> None:
    api, cache, recorder = FakeTavily(), DictCache(), ListRecorder()
    search = TavilyWebSearch(api, clock=clock, cache=cache, recorder=recorder)
    query = WebSearchQuery(
        query="apollo about", include_domains=["apollohospitals.com"], include_raw_content=True
    )
    first = await search.search(query)
    second = await search.search(query)
    assert api.calls[0][1]["include_raw_content"] == "markdown"
    assert api.calls[0][1]["include_domains"] == ["apollohospitals.com"]
    assert first.hits[0].raw_content == "# About" and first.credits_used == 1
    assert second.from_cache and second.credits_used == 0
    assert len(api.calls) == 1
    assert search.meter.spent == 1 and [charge.cache_hit for charge in recorder.charges] == [False, True]


async def test_tavily_extract_estimates_credits_from_successes(clock: Any) -> None:
    search = TavilyWebSearch(FakeTavily(), clock=clock)
    response = await search.extract(
        ExtractRequest(urls=["https://a.com/", "https://a.com/x"], depth="advanced")
    )
    assert [doc.url for doc in response.results] == ["https://a.com/"]
    assert response.failed[0].error == "Failed to fetch url"
    assert response.credits_used == 2


async def test_tavily_map_and_credit_limit_errors(clock: Any) -> None:
    api = FakeTavily()
    search = TavilyWebSearch(api, clock=clock)
    mapped = await search.map(SiteMapRequest(url="https://a.com", max_depth=2, limit=50))
    assert len(mapped.urls) == 15 and mapped.credits_used == 2
    api.fail = UsageLimitExceededError("plan limit")
    with pytest.raises(WebSearchError) as error:
        await search.search(WebSearchQuery(query="x"))
    assert error.value.kind == "credit_limit" and error.value.disables_provider


def test_cache_key_is_order_insensitive() -> None:
    assert cache_key("search", {"a": 1, "b": 2}) == cache_key("search", {"b": 2, "a": 1})
