import math
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.adapters.freshness.base import (
    FreshnessDeps,
    clean_text,
    failed_result,
    get_json,
    respect_interval,
)
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
RETRACTION_PUBTYPES = frozenset({"retracted publication", "retraction of publication"})
DATE_FORMATS = ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y %b %d", "%Y %b", "%Y")
MAX_RECORDS = 20


@freshness_source_registry.register("pubmed")
class PubMedSource:
    key: ClassVar[str] = "pubmed"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=True,
        supports_language=False,
        supports_region=False,
        returns_dates="date",
        cost_unit="free",
        min_interval_seconds=0.34,
        content_policy="metadata_and_link_only",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps
        api_key = deps.settings.freshness.ncbi_api_key
        self._api_key = api_key.get_secret_value() if api_key is not None else None
        self._min_interval = 0.1 if self._api_key else self.capabilities.min_interval_seconds

    def _common_params(self) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "db": "pubmed",
            "retmode": "json",
            "tool": self.deps.settings.news.ncbi_tool,
            "email": self.deps.settings.news.ncbi_email,
        }
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def _call(self, endpoint: str, params: dict[str, str | int]) -> httpx.Response:
        await respect_interval(self.deps, self.key, self._min_interval)
        return await get_json(self.deps, f"{EUTILS_BASE}/{endpoint}", {**self._common_params(), **params})

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        days = max(1, math.ceil((self.deps.clock.now() - since).total_seconds() / 86_400))
        try:
            search = await self._call(
                "esearch.fcgi",
                {
                    "term": query.normalized_query,
                    "reldate": days,
                    "datetype": "edat",
                    "retmax": MAX_RECORDS,
                    "sort": "pub_date",
                },
            )
            if search.status_code != 200:
                return failed_result(search.status_code, f"pubmed esearch http {search.status_code}")
            ids = search.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return NewsFetchResult(items=[], cost=0.0, status="ok")
            summary = await self._call("esummary.fcgi", {"id": ",".join(ids), "version": "2.0"})
            if summary.status_code != 200:
                return failed_result(summary.status_code, f"pubmed esummary http {summary.status_code}")
            result = summary.json().get("result", {})
        except (httpx.HTTPError, ValueError) as error:
            return failed_result(None, f"pubmed request failed: {type(error).__name__}")
        now = self.deps.clock.now()
        records = [result.get(uid) for uid in result.get("uids", ids)]
        items = [item for record in records if (item := _to_item(record, now)) is not None]
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())


def _to_item(record: dict[str, Any] | None, now: datetime) -> RawNewsItem | None:
    if not record or "uid" not in record:
        return None
    pubtypes = {str(pubtype).lower() for pubtype in record.get("pubtype", [])}
    if pubtypes & RETRACTION_PUBTYPES:
        return None
    pmid = str(record["uid"])
    external_ids = {"pmid": pmid}
    doi = _doi(record)
    if doi:
        external_ids["doi"] = doi
    if "preprint" in pubtypes:
        external_ids["pubtype"] = "preprint"
    return RawNewsItem(
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        title=clean_text(record.get("title"), 500),
        snippet=clean_text(record.get("fulljournalname") or record.get("source")),
        published_at=_published_at(record, now),
        language=_language(record.get("lang")),
        external_ids=external_ids,
    )


def _doi(record: dict[str, Any]) -> str | None:
    for article_id in record.get("articleids", []):
        if article_id.get("idtype") == "doi" and article_id.get("value"):
            return str(article_id["value"])
    location = str(record.get("elocationid") or "")
    if location.startswith("doi:"):
        return location.removeprefix("doi:").strip() or None
    return None


def _published_at(record: dict[str, Any], now: datetime) -> datetime | None:
    # pubdate is skipped on purpose: it can name a future print issue
    entrez = next(
        (entry.get("date") for entry in record.get("history", []) if entry.get("pubstatus") == "entrez"),
        None,
    )
    for candidate in (record.get("epubdate"), entrez, record.get("sortpubdate")):
        parsed = _parse_date(candidate)
        if parsed is not None and parsed <= now:
            return parsed
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _language(codes: list[str] | None) -> str | None:
    mapping = {"eng": "en", "hin": "hi", "spa": "es", "fre": "fr", "ger": "de", "chi": "zh", "jpn": "ja"}
    if not codes:
        return None
    return mapping.get(codes[0])
