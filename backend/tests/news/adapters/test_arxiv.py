from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.arxiv import ARXIV_API, ArxivSource
from app.adapters.freshness.base import FreshnessDeps
from tests.news.adapters.conftest import SINCE, planned

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2611.01234v1</id>
<published>2026-11-18T17:59:59Z</published><updated>2026-11-18T17:59:59Z</updated>
<title>Evaluating Claude Fable 5.2 on reasoning</title>
<summary>We evaluate the model.</summary>
<link href="http://arxiv.org/abs/2611.01234v1" rel="alternate" type="text/html"/></entry>
<entry><id>http://arxiv.org/abs/2610.00001v2</id>
<published>2026-10-01T00:00:00Z</published><updated>2026-10-01T00:00:00Z</updated>
<title>Old paper</title><summary>Old.</summary>
<link href="http://arxiv.org/abs/2610.00001v2" rel="alternate" type="text/html"/></entry>
</feed>"""


@respx.mock
async def test_arxiv_query_and_parse(deps: FreshnessDeps) -> None:
    route = respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=ATOM))
    result = await ArxivSource(deps).fetch(planned("arxiv", "Claude Fable"), SINCE)
    assert result.status == "ok"
    [item] = result.items
    assert item.external_ids == {"arxiv_id": "2611.01234v1"}
    assert item.url == "http://arxiv.org/abs/2611.01234v1"
    assert item.published_at == datetime(2026, 11, 18, 17, 59, 59, tzinfo=UTC)
    params = route.calls.last.request.url.params
    assert params["search_query"] == '(cat:cs.CL OR cat:cs.AI OR cat:cs.LG) AND all:"Claude Fable"'
    assert params["sortBy"] == "submittedDate"


@respx.mock
async def test_arxiv_outage(deps: FreshnessDeps) -> None:
    respx.get(ARXIV_API).mock(return_value=httpx.Response(503))
    assert (await ArxivSource(deps).fetch(planned("arxiv", "x"), SINCE)).status == "error"
