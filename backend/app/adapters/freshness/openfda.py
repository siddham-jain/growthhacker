import re
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.adapters.freshness.base import FreshnessDeps, clean_text, failed_result, get_json, respect_interval
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

DRUGSFDA_API = "https://api.fda.gov/drug/drugsfda.json"
OVERVIEW_URL = "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo="
NON_DIGITS = re.compile(r"\D")
LIMIT = 100


@freshness_source_registry.register("openfda")
class OpenFdaApprovalsSource:
    key: ClassVar[str] = "openfda"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=True,
        supports_language=False,
        supports_region=False,
        returns_dates="date",
        cost_unit="free",
        min_interval_seconds=0.25,
        content_policy="metadata_and_link_only",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps
        api_key = deps.settings.freshness.openfda_api_key
        self._api_key = api_key.get_secret_value() if api_key is not None else None

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        start, end = since.strftime("%Y%m%d"), self.deps.clock.now().strftime("%Y%m%d")
        params: dict[str, str | int] = {
            "search": (
                "submissions.submission_type:ORIG+AND+submissions.submission_status:AP"
                f"+AND+submissions.submission_status_date:[{start}+TO+{end}]"
            ),
            "limit": LIMIT,
        }
        if self._api_key:
            params["api_key"] = self._api_key
        await respect_interval(self.deps, self.key, self.capabilities.min_interval_seconds)
        try:
            response = await get_json(self.deps, DRUGSFDA_API, params)
            if response.status_code == 404 and "no matches" in response.text.lower():
                return NewsFetchResult(items=[], cost=0.0, status="ok")
            if response.status_code != 200:
                return failed_result(response.status_code, f"openfda http {response.status_code}")
            results: list[dict[str, Any]] = response.json().get("results", [])
        except (httpx.HTTPError, ValueError) as error:
            return failed_result(None, f"openfda request failed: {type(error).__name__}")
        items = [item for result in results if (item := _to_item(result, start, end)) is not None]
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())


def _to_item(result: dict[str, Any], start: str, end: str) -> RawNewsItem | None:
    application = str(result.get("application_number") or "")
    approval = next(
        (
            submission
            for submission in result.get("submissions", [])
            if submission.get("submission_type") == "ORIG"
            and submission.get("submission_status") == "AP"
            and start <= str(submission.get("submission_status_date", "")) <= end
        ),
        None,
    )
    if not application or approval is None:
        return None
    openfda = result.get("openfda", {})
    products = result.get("products", [])
    name = (
        (openfda.get("brand_name") or [None])[0]
        or (products[0].get("brand_name") if products else None)
        or (openfda.get("generic_name") or ["unnamed product"])[0]
    )
    sponsor = result.get("sponsor_name") or "unknown sponsor"
    approved_on = datetime.strptime(approval["submission_status_date"], "%Y%m%d").replace(tzinfo=UTC)
    return RawNewsItem(
        url=OVERVIEW_URL + NON_DIGITS.sub("", application),
        title=clean_text(f"FDA approves {name} ({sponsor})", 500),
        snippet="",
        published_at=approved_on,
        language="en",
        external_ids={"fda_application": application},
    )
