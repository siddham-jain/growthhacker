import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.tavily import TavilyDomainSource, TavilyNewsSource
from tests.conftest import make_settings
from tests.news.adapters.conftest import SINCE, planned

SEARCH_URL = "https://api.tavily.com/search"
PAYLOAD = {
    "query": "Anthropic Claude Fable 5.2",
    "results": [
        {
            "url": "https://www.theverge.com/ai/claude-fable-5-2",
            "title": "Anthropic releases <b>Claude Fable 5.2</b>",
            "content": "Anthropic on Monday released Claude Fable 5.2, its newest model.",
            "score": 0.91,
            "published_date": "Mon, 16 Nov 2026 18:27:56 GMT",
        },
        {
            "url": "https://example.com/undated",
            "title": "Undated post",
            "content": "x" * 2000,
            "score": 0.4,
            "published_date": None,
        },
    ],
    "usage": {"credits": 1},
    "request_id": "req-123",
}


@respx.mock
async def test_news_search_maps_results_and_request(deps: FreshnessDeps) -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    result = await TavilyNewsSource(deps).fetch(
        planned("tavily_news", "anthropic claude fable", time_range="day", language="en"), SINCE
    )
    assert result.status == "ok"
    assert result.cost == 1.0
    assert result.provider_request_id == "req-123"
    first, second = result.items
    assert first.title == "Anthropic releases Claude Fable 5.2"
    assert first.published_at == datetime(2026, 11, 16, 18, 27, 56, tzinfo=UTC)
    assert first.provider_score == 0.91
    assert second.published_at is None
    assert len(second.snippet) == 1500
    sent = json.loads(route.calls.last.request.content)
    assert sent["topic"] == "news"
    assert sent["search_depth"] == "basic"
    assert sent["time_range"] == "day"
    assert sent["filter_by_published_date"] is True
    assert sent["auto_parameters"] is False
    assert sent["include_raw_content"] is False
    assert sent["language"] == "en"
    assert {"youtube.com", "instagram.com", "x.com"} <= set(sent["exclude_domains"])
    assert route.calls.last.request.headers["authorization"] == "Bearer tvly-test"


@respx.mock
async def test_domain_search_restricts_domains(deps: FreshnessDeps) -> None:
    route = respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    result = await TavilyDomainSource(deps).fetch(
        planned("tavily_domains", "claude", include_domains=["anthropic.com"]), SINCE
    )
    assert result.status == "ok"
    assert result.cost == 1.0
    sent = json.loads(route.calls.last.request.content)
    assert sent["topic"] == "general"
    assert sent["include_domains"] == ["anthropic.com"]
    assert sent["include_domains_mode"] == "restrict"
    assert sent["time_range"] == "week"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(429, "rate_limited"), (432, "exhausted"), (433, "exhausted"), (500, "error"), (401, "error")],
)
@respx.mock
async def test_failures_map_to_statuses(deps: FreshnessDeps, status_code: int, expected: str) -> None:
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(status_code, json={"detail": {"error": "limit"}}))
    result = await TavilyNewsSource(deps).fetch(planned("tavily_news", "q"), SINCE)
    assert result.status == expected
    assert result.items == []
    assert result.cost == 0.0
    assert result.error


@respx.mock
async def test_timeout_is_an_error(deps: FreshnessDeps) -> None:
    respx.post(SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
    result = await TavilyNewsSource(deps).fetch(planned("tavily_news", "q"), SINCE)
    assert result.status == "error"


@respx.mock
async def test_missing_key_never_calls_the_network(deps: FreshnessDeps) -> None:
    route = respx.post(SEARCH_URL)
    no_key = FreshnessDeps(http=deps.http, settings=make_settings(), clock=deps.clock)
    source = TavilyNewsSource(no_key)
    result = await source.fetch(planned("tavily_news", "q"), SINCE)
    assert result.status == "error"
    assert result.error == "tavily api key missing"
    assert not route.called
    assert (await source.health()).healthy is False
