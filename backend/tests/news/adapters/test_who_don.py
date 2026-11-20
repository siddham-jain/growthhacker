from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.who_don import WHO_DON_API, WhoOutbreakNewsSource
from tests.news.adapters.conftest import SINCE, planned

ODATA = {
    "value": [
        {
            "Title": "Dengue - Global situation",
            "PublicationDate": "2026-11-17T10:00:00Z",
            "ItemDefaultUrl": "/2026-DON627",
            "DonId": "2026-DON627",
        },
        {
            "Title": "Old outbreak",
            "PublicationDate": "2026-09-01T00:00:00Z",
            "ItemDefaultUrl": "/2026-DON600",
            "DonId": "2026-DON600",
        },
    ]
}


@respx.mock
async def test_metadata_and_link_only(deps: FreshnessDeps) -> None:
    route = respx.get(WHO_DON_API).mock(return_value=httpx.Response(200, json=ODATA))
    result = await WhoOutbreakNewsSource(deps).fetch(planned("who_don", "who_don"), SINCE)
    [item] = result.items
    assert item.url == "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON627"
    assert item.snippet == ""
    assert item.external_ids == {"don_id": "2026-DON627"}
    assert item.published_at == datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    assert route.calls.last.request.url.params["$top"] == "20"


@respx.mock
async def test_who_outage(deps: FreshnessDeps) -> None:
    respx.get(WHO_DON_API).mock(return_value=httpx.Response(502))
    assert (await WhoOutbreakNewsSource(deps).fetch(planned("who_don", "who_don"), SINCE)).status == "error"
