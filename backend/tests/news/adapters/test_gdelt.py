from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.gdelt import GDELT_DOC_API, GdeltDocSource
from tests.news.adapters.conftest import SINCE, planned

PAYLOAD = {
    "articles": [
        {
            "url": "https://www.thehindu.com/news/dengue-advisory",
            "title": "Health ministry issues dengue advisory",
            "seendate": "20261117T101500Z",
            "domain": "thehindu.com",
        }
    ]
}


@respx.mock
async def test_artlist_parse_and_query(deps: FreshnessDeps) -> None:
    route = respx.get(GDELT_DOC_API).mock(return_value=httpx.Response(200, json=PAYLOAD))
    result = await GdeltDocSource(deps).fetch(planned("gdelt", "dengue advisory", source_country="IN"), SINCE)
    [item] = result.items
    assert item.published_at == datetime(2026, 11, 17, 10, 15, tzinfo=UTC)
    params = route.calls.last.request.url.params
    assert params["query"] == "dengue advisory sourcecountry:IN"
    assert params["timespan"] == "7d"
    assert params["mode"] == "artlist"


@respx.mock
async def test_plain_text_rate_limit(deps: FreshnessDeps) -> None:
    respx.get(GDELT_DOC_API).mock(
        return_value=httpx.Response(200, text="Please limit requests to one every 5 seconds")
    )
    result = await GdeltDocSource(deps).fetch(planned("gdelt", "q"), SINCE)
    assert result.status == "rate_limited"


@respx.mock
async def test_http_429(deps: FreshnessDeps) -> None:
    respx.get(GDELT_DOC_API).mock(return_value=httpx.Response(429, text="slow down"))
    assert (await GdeltDocSource(deps).fetch(planned("gdelt", "q"), SINCE)).status == "rate_limited"
