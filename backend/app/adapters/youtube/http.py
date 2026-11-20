from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from pydantic import SecretStr

from app.adapters.youtube.errors import KeyRejected, NotSent, Transient, classify_error
from app.adapters.youtube.fields import assert_policy_safe
from app.core.logging import redact_text
from app.ports.youtube import RequestContext


@dataclass(frozen=True)
class ApiRequest:
    endpoint: str
    url: str
    params: Mapping[str, str]
    ctx: RequestContext
    etag: str | None = None
    bearer: SecretStr | None = None
    cache_outcome: Literal["miss", "coalesced"] = "miss"


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class RequestHandler(Protocol):
    async def send(self, request: ApiRequest) -> ApiResponse: ...


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class HttpHandler:
    """the only place that talks to google; the api key travels as a query parameter and is never logged."""

    def __init__(
        self, client: httpx.AsyncClient, api_key: SecretStr | None, *, timeout_s: float = 15
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))

    async def send(self, request: ApiRequest) -> ApiResponse:
        assert_policy_safe(request.params)
        params = dict(request.params)
        headers = {"Accept": "application/json"}
        if request.bearer is not None:
            headers["Authorization"] = f"Bearer {request.bearer.get_secret_value()}"
        elif self._api_key is not None:
            params["key"] = self._api_key.get_secret_value()
        else:
            raise KeyRejected("no youtube api key configured", reason="keyMissing")
        if request.etag:
            # the body etag is sent unquoted; the quoted rfc form returned 200 in live checks
            headers["If-None-Match"] = request.etag
        try:
            response = await self._client.get(
                request.url, params=params, headers=headers, timeout=self._timeout
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            raise NotSent(f"{request.endpoint}: {type(error).__name__}") from None
        except httpx.TransportError as error:
            raise Transient(
                f"{request.endpoint}: {type(error).__name__}: {redact_text(str(error))}"
            ) from None
        if response.status_code == 304:
            return ApiResponse(status=304)
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if response.status_code >= 400:
            raise classify_error(response.status_code, body, retry_after_s=_retry_after(response))
        return ApiResponse(status=response.status_code, body=body if isinstance(body, dict) else {})
