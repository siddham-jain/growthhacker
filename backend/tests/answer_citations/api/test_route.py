import pytest

from tests.api.conftest import Harness
from tests.api_services.conftest import tenant

pytestmark = pytest.mark.db


async def test_answer_citations_route_is_tenant_scoped_and_strict(harness: Harness) -> None:
    owner = await tenant(harness, "citeOwner")
    stranger = await tenant(harness, "citeStranger")

    empty = await harness.client.get(f"{owner.path}/answer-citations", headers=owner.headers)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["lens"] == "conversion" and body["signal"] == "cited_in_ai_answers"
    assert body["questions"] == [] and body["counts"]["questions_checked"] == 0
    assert body["last_checked_at"] is None

    typo = await harness.client.get(f"{owner.path}/answer-citations?window=7", headers=owner.headers)
    assert typo.status_code == 400

    foreign = await harness.client.get(f"{owner.path}/answer-citations", headers=stranger.headers)
    assert foreign.status_code in (403, 404)
