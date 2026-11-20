import httpx
import pytest
import respx
from pydantic import SecretStr

from app.adapters.youtube.errors import CircuitOpen, CommentsDisabled, KeyRejected, NotSent, Transient
from app.adapters.youtube.http import ApiRequest, HttpHandler
from app.adapters.youtube.middleware import build_handler_chain
from app.core.clock import FrozenClock
from app.core.config import YouTubeRetrySettings
from app.domain.youtube import QuotaBucket, QuotaPriority, QuotaUsage
from app.ports.quota import QuotaExhausted, QuotaReservation
from app.services.youtube.context import request_context
from tests.youtube.fixtures import NOW

URL = "https://yt.test/youtube/v3/videos"
CTX = request_context(QuotaPriority.interactive)


class FakeQuota:
    def __init__(self) -> None:
        self.reserved = 0
        self.refunded = 0
        self.exhausted: list[QuotaBucket] = []

    async def reserve(self, bucket, units, ctx, endpoint) -> QuotaReservation:
        if bucket in self.exhausted:
            raise QuotaExhausted(bucket, NOW)
        self.reserved += units
        return QuotaReservation(bucket=bucket, units=units, used_after=self.reserved, endpoint=endpoint)

    async def refund(self, reservation, ctx) -> None:
        self.refunded += reservation.units

    async def mark_exhausted(self, bucket):
        self.exhausted.append(bucket)
        return NOW


class FakeLedger:
    def __init__(self) -> None:
        self.rows: list[QuotaUsage] = []

    async def record(self, usage: QuotaUsage, *, cache_key: str | None = None) -> None:
        self.rows.append(usage)


def error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    )


@pytest.fixture
async def chain():
    async with httpx.AsyncClient() as client:
        quota, ledger, sleeps = FakeQuota(), FakeLedger(), []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        http = HttpHandler(client, SecretStr("AIzaTESTKEY000000000000000000000000000"))
        handler = build_handler_chain(
            http,
            quota=quota,
            ledger=ledger,
            clock=FrozenClock(NOW),
            settings=YouTubeRetrySettings(),
            sleep=sleep,
        )
        yield handler, quota, ledger, sleeps


def request(endpoint: str = "videos.list") -> ApiRequest:
    return ApiRequest(endpoint=endpoint, url=URL, params={"part": "snippet", "id": "abc"}, ctx=CTX)


@respx.mock
async def test_retries_transient_errors_and_meters_every_attempt(chain) -> None:
    handler, quota, ledger, sleeps = chain
    route = respx.get(URL).mock(
        side_effect=[
            error(503, "backendError"),
            error(403, "rateLimitExceeded"),
            httpx.Response(200, json={"items": []}),
        ]
    )
    response = await handler.send(request())
    assert response.status == 200 and route.call_count == 3
    assert quota.reserved == 3 and len(ledger.rows) == 3
    assert [row.http_status for row in ledger.rows] == [503, 403, 200]
    assert len(sleeps) == 2 and all(0 <= delay <= 20 for delay in sleeps)


@respx.mock
async def test_retry_after_is_honoured_and_attempts_are_bounded(chain) -> None:
    handler, quota, _, sleeps = chain
    respx.get(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "3"}, json={}))
    with pytest.raises(Transient):
        await handler.send(request())
    assert quota.reserved == 4 and sleeps == [3.0, 3.0, 3.0]


@pytest.mark.parametrize(
    ("status", "reason", "raised"),
    [
        (403, "quotaExceeded", QuotaExhausted),
        (400, "keyInvalid", KeyRejected),
        (403, "commentsDisabled", CommentsDisabled),
    ],
)
@respx.mock
async def test_never_retries_final_errors(chain, status, reason, raised) -> None:
    handler, quota, ledger, _ = chain
    route = respx.get(URL).mock(return_value=error(status, reason))
    with pytest.raises(raised):
        await handler.send(request())
    assert route.call_count == 1 and quota.reserved == 1 and ledger.rows[0].reason == reason
    if raised is QuotaExhausted:
        assert quota.exhausted == [QuotaBucket.main]


@respx.mock
async def test_connect_errors_are_refunded_and_retried(chain) -> None:
    handler, quota, ledger, _ = chain
    respx.get(URL).mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json={})])
    await handler.send(request())
    assert quota.reserved == 2 and quota.refunded == 1 and len(ledger.rows) == 1


@respx.mock
async def test_read_timeouts_are_not_refunded(chain) -> None:
    handler, quota, _, _ = chain
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(Transient):
        await handler.send(request())
    assert quota.reserved == 4 and quota.refunded == 0


@respx.mock
async def test_key_circuit_fails_fast_after_rejection(chain) -> None:
    handler, quota, _, _ = chain
    route = respx.get(URL).mock(return_value=error(403, "accessNotConfigured"))
    with pytest.raises(KeyRejected):
        await handler.send(request())
    with pytest.raises(KeyRejected):
        await handler.send(request())
    assert route.call_count == 1 and quota.reserved == 1


@respx.mock
async def test_endpoint_circuit_opens_after_5xx_burst(chain) -> None:
    handler, _, _, _ = chain
    route = respx.get(URL).mock(return_value=error(500, "backendError"))
    with pytest.raises(Transient):
        await handler.send(request())
    with pytest.raises(CircuitOpen):
        await handler.send(request())
    assert route.call_count == 5
    with pytest.raises(CircuitOpen):
        await handler.send(request())
    assert route.call_count == 5


@respx.mock
async def test_api_key_never_appears_in_errors(chain) -> None:
    handler, _, _, _ = chain
    respx.get(URL).mock(
        side_effect=httpx.ReadError("boom at https://yt.test?key=AIzaTESTKEY000000000000000000000000000")
    )
    with pytest.raises(Transient) as raised:
        await handler.send(request())
    assert "AIzaTESTKEY" not in str(raised.value)


@respx.mock
async def test_etag_is_sent_unquoted_and_304_is_not_modified(chain) -> None:
    handler, _, _, _ = chain
    route = respx.get(URL).mock(return_value=httpx.Response(304))
    response = await handler.send(
        ApiRequest(endpoint="playlistItems.list", url=URL, params={}, ctx=CTX, etag="Xyz123")
    )
    assert response.not_modified
    assert route.calls[0].request.headers["If-None-Match"] == "Xyz123"
    assert "key=" in str(route.calls[0].request.url)


async def test_not_sent_is_retryable() -> None:
    assert NotSent.retryable and not KeyRejected.retryable
