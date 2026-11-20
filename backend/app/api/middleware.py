import json
import re
import time
import uuid
from typing import Any

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import PROBLEM_BASE, PROBLEM_MEDIA_TYPE
from app.core.logging import get_logger

logger = get_logger("app.api.access")

REQUEST_ID_HEADER = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestContextMiddleware:
    """request id + structured access log; logs the path only, so ?token= never reaches the log."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status: dict[str, int] = {}

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                rate = scope.get("state", {}).get("rate_headers")
                if rate:
                    headers["RateLimit-Policy"] = rate["RateLimit-Policy"]
                    headers["RateLimit"] = rate["RateLimit"]
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, send_with_context)
        finally:
            logger.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status.get("code"),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.unbind_contextvars("request_id")


class BodySizeLimitMiddleware:
    """rejects request bodies above the limit with a problem response (413)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return
        received = 0
        rejected = False

        async def counting_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    raise _TooLarge
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _TooLarge:
            if rejected:
                await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id", "")
        body = _problem_json(scope["path"], request_id, self.max_bytes)
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", PROBLEM_MEDIA_TYPE.encode()),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


def _problem_json(path: str, request_id: str, limit: int) -> bytes:
    problem: dict[str, Any] = {
        "type": PROBLEM_BASE + "payload_too_large",
        "title": "The request body is too large",
        "status": 413,
        "detail": f"request bodies are limited to {limit} bytes",
        "instance": path,
        "code": "payload_too_large",
        "request_id": request_id,
    }
    return json.dumps(problem).encode()
