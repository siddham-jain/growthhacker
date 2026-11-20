from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.pubmed import EUTILS_BASE, PubMedSource
from tests.news.adapters.conftest import SINCE, planned

ESEARCH = {"esearchresult": {"idlist": ["41000001", "41000002", "41000003"]}}
ESUMMARY = {
    "result": {
        "uids": ["41000001", "41000002", "41000003"],
        "41000001": {
            "uid": "41000001",
            "title": "Dengue warning signs in Indian children",
            "pubdate": "2027 Mar",
            "epubdate": "2026 Oct 28",
            "sortpubdate": "2027/03/01 00:00",
            "fulljournalname": "Indian Journal of Pediatrics",
            "pubtype": ["Journal Article"],
            "lang": ["eng"],
            "elocationid": "doi: 10.1000/ijp.2026.1",
            "articleids": [{"idtype": "pubmed", "value": "41000001"}],
        },
        "41000002": {
            "uid": "41000002",
            "title": "A retracted dengue cure claim",
            "epubdate": "2026 Nov 10",
            "fulljournalname": "Journal X",
            "pubtype": ["Journal Article", "Retracted Publication"],
        },
        "41000003": {
            "uid": "41000003",
            "title": "Dengue vaccine preprint",
            "epubdate": "",
            "history": [{"pubstatus": "entrez", "date": "2026/11/18 00:00"}],
            "fulljournalname": "medRxiv",
            "pubtype": ["Preprint"],
            "articleids": [{"idtype": "doi", "value": "10.1101/2026.11.01"}],
        },
    }
}


@respx.mock
async def test_search_then_summary(deps: FreshnessDeps) -> None:
    search = respx.get(f"{EUTILS_BASE}/esearch.fcgi").mock(return_value=httpx.Response(200, json=ESEARCH))
    summary = respx.get(f"{EUTILS_BASE}/esummary.fcgi").mock(return_value=httpx.Response(200, json=ESUMMARY))
    result = await PubMedSource(deps).fetch(planned("pubmed", "dengue india"), SINCE)
    assert result.status == "ok"
    assert [item.external_ids["pmid"] for item in result.items] == ["41000001", "41000003"]
    article, preprint = result.items
    assert article.published_at == datetime(2026, 10, 28, tzinfo=UTC)
    assert article.url == "https://pubmed.ncbi.nlm.nih.gov/41000001/"
    assert article.snippet == "Indian Journal of Pediatrics"
    assert article.external_ids["doi"] == "10.1000/ijp.2026.1"
    assert article.language == "en"
    assert preprint.published_at == datetime(2026, 11, 18, tzinfo=UTC)
    assert preprint.external_ids == {"pmid": "41000003", "doi": "10.1101/2026.11.01", "pubtype": "preprint"}
    params = search.calls.last.request.url.params
    assert params["reldate"] == "7"
    assert params["datetype"] == "edat"
    assert params["tool"] == "growthhacker"
    assert "api_key" not in params
    assert summary.calls.last.request.url.params["version"] == "2.0"


@respx.mock
async def test_empty_search_skips_summary(deps: FreshnessDeps) -> None:
    respx.get(f"{EUTILS_BASE}/esearch.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": []}})
    )
    summary = respx.get(f"{EUTILS_BASE}/esummary.fcgi")
    result = await PubMedSource(deps).fetch(planned("pubmed", "nothing"), SINCE)
    assert result.status == "ok"
    assert result.items == []
    assert not summary.called


@respx.mock
async def test_rate_limit_and_outage(deps: FreshnessDeps) -> None:
    respx.get(f"{EUTILS_BASE}/esearch.fcgi").mock(return_value=httpx.Response(429))
    assert (await PubMedSource(deps).fetch(planned("pubmed", "q"), SINCE)).status == "rate_limited"
    respx.get(f"{EUTILS_BASE}/esearch.fcgi").mock(return_value=httpx.Response(503))
    assert (await PubMedSource(deps).fetch(planned("pubmed", "q"), SINCE)).status == "error"
