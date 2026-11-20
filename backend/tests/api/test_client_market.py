import uuid

import pytest

from app.api import ids
from app.models.tenancy import Client
from tests.api.conftest import Harness

pytestmark = pytest.mark.db

BROWSER = {"country": "US", "language": "en-US", "timezone": "Asia/Calcutta"}


async def create(harness: Harness, headers: dict[str, str], name: str, **body: object) -> dict:
    response = await harness.client.post(
        "/api/v1/clients",
        json={"name": name, "start_onboarding": False, **body},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return response.json()["client"]


async def stored(harness: Harness, client_id: str) -> Client:
    async with harness.container.session_factory() as session:
        client = await session.get(Client, ids.decode(ids.CLIENT, client_id))
    assert client is not None
    return client


async def test_market_is_explicit_only_when_the_user_picks_it(harness: Harness) -> None:
    account = await harness.account("market")
    unknown = await create(harness, account.headers, "Medanta", website_url="https://www.medanta.org")
    row = await stored(harness, unknown["id"])
    assert row.country is None and not row.settings["market_explicit"]

    hinted = await create(harness, account.headers, "Narayana", market=BROWSER, market_explicit=False)
    row = await stored(harness, hinted["id"])
    assert row.country == "US" and not row.settings["market_explicit"]

    picked = await create(harness, account.headers, "Replit", market=BROWSER)
    assert (await stored(harness, picked["id"])).settings["market_explicit"]

    patched = await harness.client.patch(
        f"/api/v1/clients/{hinted['id']}",
        json={"market": {"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"}},
        headers=account.headers,
    )
    assert patched.status_code == 200, patched.text
    row = await stored(harness, hinted["id"])
    assert row.country == "IN" and row.settings["market_explicit"]
