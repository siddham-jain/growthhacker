import uuid
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.services import YOUTUBE_OAUTH, OAuthOutcome
from tests.api.conftest import PASSWORD, Harness
from tests.api_services.conftest import tenant

pytestmark = pytest.mark.db
CALLBACK = "/api/v1/integrations/youtube/callback"


class RecordingOAuth:
    def __init__(self) -> None:
        self.completed: list[tuple[str, uuid.UUID]] = []

    async def start(self, scope, user_id) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth?state=tok-bound&client_id=x"

    async def complete(self, state: str, code: str, *, user_id: uuid.UUID) -> OAuthOutcome:
        self.completed.append((state, user_id))
        return OAuthOutcome(status="connected")

    async def disconnect(self, scope) -> bool:
        return True


async def sign_in(harness: Harness, email: str) -> None:
    harness.client.cookies.clear()
    login = await harness.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text


async def start(harness: Harness, client_id: str) -> None:
    connect = await harness.client.get(
        "/api/v1/integrations/youtube/connect", params={"client_id": client_id}
    )
    assert connect.status_code == 200, connect.text
    assert parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"] == ["tok-bound"]


@pytest.mark.parametrize("victim_session", ["none", "other_user"])
async def test_a_callback_link_only_completes_in_the_session_that_started_it(
    harness: Harness,
    victim_session: str,
) -> None:
    attacker = await tenant(harness, "oauthatk")
    victim = await tenant(harness, "oauthvic")
    fake = RecordingOAuth()
    harness.state.override_service(YOUTUBE_OAUTH, fake)
    await sign_in(harness, attacker.account.email)
    await start(harness, attacker.client["id"])

    harness.client.cookies.clear()
    if victim_session == "other_user":
        await sign_in(harness, victim.account.email)
    callback = await harness.client.get(
        CALLBACK, params={"state": "tok-bound", "code": "attacker-code"}, follow_redirects=False
    )
    assert "reason=session_mismatch" in callback.headers["location"]
    assert fake.completed == []
    replay = await harness.client.get(
        CALLBACK, params={"state": "tok-bound", "code": "x"}, follow_redirects=False
    )
    assert "reason=invalid_state" in replay.headers["location"]
