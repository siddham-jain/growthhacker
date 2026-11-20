from datetime import timedelta

import httpx
import pytest

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.pubmed import PubMedSource
from app.adapters.freshness.rss import RssSource
from app.adapters.freshness.tavily import TavilyNewsSource
from app.core.clock import SystemClock
from app.core.config import Settings
from app.ports.freshness import PlannedQuery

pytestmark = pytest.mark.live


def planned(source_key: str, query: str, **params) -> PlannedQuery:
    return PlannedQuery(
        source_key=source_key, normalized_query=query, params=params, query_hash="live", cadence_minutes=360
    )


@pytest.fixture
async def live_deps():
    # reads the repo .env directly; the autouse fixture only strips process env vars
    async with httpx.AsyncClient() as http:
        yield FreshnessDeps(http=http, settings=Settings(), clock=SystemClock())


async def test_tavily_news_single_basic_query(live_deps: FreshnessDeps) -> None:
    if live_deps.settings.tavily.api_key is None:
        pytest.skip("tavily key not configured")
    since = live_deps.clock.now() - timedelta(days=7)
    result = await TavilyNewsSource(live_deps).fetch(
        planned("tavily_news", "Anthropic Claude model release", time_range="week", max_results=5), since
    )
    assert result.status == "ok", result.error
    assert 0 < result.cost <= 5
    assert len(result.items) <= 5
    for item in result.items:
        assert item.url.startswith("http")
        assert len(item.snippet) <= 1500


async def test_pubmed_single_search_and_summary(live_deps: FreshnessDeps) -> None:
    since = live_deps.clock.now() - timedelta(days=30)
    result = await PubMedSource(live_deps).fetch(planned("pubmed", "dengue India"), since)
    assert result.status == "ok", result.error
    for item in result.items:
        assert item.url.startswith("https://pubmed.ncbi.nlm.nih.gov/")
        assert "pmid" in item.external_ids
        assert item.published_at is None or item.published_at <= live_deps.clock.now()


async def test_rss_single_feed(live_deps: FreshnessDeps) -> None:
    since = live_deps.clock.now() - timedelta(days=60)
    result = await RssSource(live_deps).fetch(
        planned("rss", "openai_news", url="https://openai.com/news/rss.xml"), since
    )
    assert result.status == "ok", result.error
    assert all(item.url.startswith("https://") for item in result.items)
