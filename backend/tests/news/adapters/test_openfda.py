from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.openfda import DRUGSFDA_API, OpenFdaApprovalsSource
from tests.news.adapters.conftest import SINCE, planned

PAYLOAD = {
    "results": [
        {
            "application_number": "NDA219999",
            "sponsor_name": "ACME PHARMA",
            "openfda": {"brand_name": ["DENGUARD"], "generic_name": ["denguvax"]},
            "products": [{"brand_name": "DENGUARD"}],
            "submissions": [
                {"submission_type": "SUPPL", "submission_status": "AP", "submission_status_date": "20261118"},
                {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20261116"},
            ],
        },
        {
            "application_number": "ANDA000001",
            "sponsor_name": "OLD CO",
            "submissions": [
                {"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20190101"}
            ],
        },
    ]
}


@respx.mock
async def test_approvals_in_range(deps: FreshnessDeps) -> None:
    route = respx.get(DRUGSFDA_API).mock(return_value=httpx.Response(200, json=PAYLOAD))
    result = await OpenFdaApprovalsSource(deps).fetch(planned("openfda", "drugsfda_approvals"), SINCE)
    [item] = result.items
    assert item.title == "FDA approves DENGUARD (ACME PHARMA)"
    assert item.url.endswith("ApplNo=219999")
    assert item.published_at == datetime(2026, 11, 16, tzinfo=UTC)
    assert item.external_ids == {"fda_application": "NDA219999"}
    assert "[20261113+TO+20261120]" in route.calls.last.request.url.params["search"]


@respx.mock
async def test_no_matches_is_ok(deps: FreshnessDeps) -> None:
    respx.get(DRUGSFDA_API).mock(
        return_value=httpx.Response(
            404, json={"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
        )
    )
    result = await OpenFdaApprovalsSource(deps).fetch(planned("openfda", "drugsfda_approvals"), SINCE)
    assert result.status == "ok"
    assert result.items == []


@respx.mock
async def test_openfda_rate_limit(deps: FreshnessDeps) -> None:
    respx.get(DRUGSFDA_API).mock(return_value=httpx.Response(429))
    result = await OpenFdaApprovalsSource(deps).fetch(planned("openfda", "drugsfda_approvals"), SINCE)
    assert result.status == "rate_limited"
