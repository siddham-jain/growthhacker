from datetime import UTC, datetime

from app.ports.search import WebSearchHit, WebSearchResponse
from app.services.website_intel.identity.locator import DefaultBusinessLocator, in_market


def hit(url: str, snippet: str = "") -> WebSearchHit:
    return WebSearchHit(url=url, title="Clove Dental", snippet=snippet)


class FakeSearch:
    def __init__(self, hits: list[WebSearchHit]) -> None:
        self.hits = hits
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return WebSearchResponse(
            query=query, hits=self.hits, credits_used=1, from_cache=False, fetched_at=datetime.now(UTC)
        )


def test_in_market_uses_the_country_tld_or_a_country_mention():
    assert in_market("clovedental.in", hit("https://clovedental.in"), "IN", "india")
    assert in_market("clovedds.com", hit("https://clovedds.com", "Dental chain across India"), "IN", "india")
    assert not in_market("clovedds.com", hit("https://clovedds.com", "Los Angeles dentist"), "IN", "india")
    assert in_market("clovedds.com", hit("https://clovedds.com"), None, None)


async def test_name_only_search_prefers_the_business_in_the_clients_market():
    search = FakeSearch(
        [hit("https://clovedds.com/", "Los Angeles dentist"), hit("https://www.clovedental.in/")]
    )
    locator = DefaultBusinessLocator.__new__(DefaultBusinessLocator)
    locator._search = search
    found = await locator._search_official_site("Clove Dental", [], "IN")
    assert found == "https://www.clovedental.in/"
    assert search.queries[0].country == "india" and "india" in search.queries[0].query
