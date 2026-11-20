from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

PROBLEM_BASE = "https://growthhacker.dev/problems/"
PROBLEM_MEDIA_TYPE = "application/problem+json"


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class Problem(BaseModel):
    """rfc 9457 problem details plus stable machine fields."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    errors: list[FieldError] | None = None
    retry_after_s: int | None = None
    extra: dict[str, Any] | None = None


class AppError(Exception):
    status = 500
    code = "internal_error"
    title = "Internal error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[FieldError] | None = None,
        retry_after_s: int | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.errors = errors
        self.retry_after_s = retry_after_s
        self.extra = extra
        self.headers = headers or {}


def _error(name: str, status: int, code: str, title: str) -> type[AppError]:
    return type(name, (AppError,), {"status": status, "code": code, "title": title})


ValidationFailed = _error("ValidationFailed", 422, "validation_failed", "The request is invalid")
BadRequest = _error("BadRequest", 400, "bad_request", "Bad request")
NotFound = _error("NotFound", 404, "not_found", "Not found")
InvalidCredentials = _error("InvalidCredentials", 401, "invalid_credentials", "Invalid credentials")
Forbidden = _error("Forbidden", 403, "forbidden", "Forbidden")
RateLimited = _error("RateLimited", 429, "rate_limited", "Too many requests")
IdempotencyKeyRequired = _error(
    "IdempotencyKeyRequired", 400, "idempotency_key_required", "An Idempotency-Key header is required"
)
IdempotencyKeyReused = _error(
    "IdempotencyKeyReused", 422, "idempotency_key_reused", "The Idempotency-Key was used with another request"
)
IdempotencyInProgress = _error(
    "IdempotencyInProgress", 409, "idempotency_request_in_progress", "The original request is still running"
)
RunInProgressError = _error("RunInProgressError", 409, "run_in_progress", "A run is already in progress")
RunCancellingError = _error("RunCancellingError", 409, "run_cancelling", "The previous run is stopping")
RunNotCancellableError = _error(
    "RunNotCancellableError", 409, "run_not_cancellable", "This run already finished"
)
RunNotRetryableError = _error("RunNotRetryableError", 409, "run_not_retryable", "This run cannot be retried")
WorkspaceBusyError = _error("WorkspaceBusyError", 409, "workspace_busy", "Too many runs are active")
ConfigInvalidError = _error("ConfigInvalidError", 422, "config_invalid", "The setup is not ready")
CompetitorsOutOfRange = _error(
    "CompetitorsOutOfRange", 422, "competitors_out_of_range", "Choose 3 to 10 competitors"
)
TopicRefsRequired = _error("TopicRefsRequired", 422, "topic_refs_required", "Add a keyword or buyer question")
ChannelUnresolvable = _error(
    "ChannelUnresolvable", 422, "channel_unresolvable", "The channel could not be found"
)
OAuthStateInvalid = _error("OAuthStateInvalid", 400, "oauth_state_invalid", "The connection link expired")
PreconditionFailed = _error("PreconditionFailed", 412, "precondition_failed", "The resource changed")
PreconditionRequired = _error("PreconditionRequired", 428, "precondition_required", "If-Match is required")
ClientDeletingError = _error("ClientDeletingError", 409, "client_deleting", "This client is being deleted")
ClientExists = _error("ClientExists", 409, "client_exists", "A client with this website or channel exists")
CursorMismatch = _error("CursorMismatch", 400, "cursor_mismatch", "The cursor belongs to another query")
PayloadTooLarge = _error("PayloadTooLarge", 413, "payload_too_large", "The request body is too large")
Overloaded = _error("Overloaded", 503, "overloaded", "The service is overloaded")
NotImplementedYet = _error(
    "NotImplementedYet", 501, "not_implemented", "This capability is not available yet"
)
Unavailable = _error("Unavailable", 503, "unavailable", "A dependency is unavailable")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def problem_response(request: Request, error: AppError) -> JSONResponse:
    request_id = _request_id(request)
    problem = Problem(
        type=PROBLEM_BASE + error.code,
        title=error.title,
        status=error.status,
        detail=error.detail,
        instance=f"{request.url.path}#{request_id}" if request_id else request.url.path,
        code=error.code,
        request_id=request_id,
        errors=error.errors,
        retry_after_s=error.retry_after_s,
        extra=error.extra,
    )
    headers = dict(error.headers)
    if error.retry_after_s is not None:
        headers["Retry-After"] = str(error.retry_after_s)
    if error.status == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=error.status,
        headers=headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _http_error(status: int) -> AppError:
    known = {400: BadRequest, 401: InvalidCredentials, 403: Forbidden, 404: NotFound, 413: PayloadTooLarge}
    if status in known:
        return known[status]()
    error = AppError(HTTPStatus(status).phrase)
    error.status = status
    error.code = HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    error.title = HTTPStatus(status).phrase
    return error


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError) -> JSONResponse:
        return problem_response(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        mapped = _http_error(error.status_code)
        if isinstance(error.detail, str) and error.detail != HTTPStatus(error.status_code).phrase:
            mapped.detail = error.detail
        mapped.headers.update(error.headers or {})
        return problem_response(request, mapped)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(
                loc=list(item.get("loc", ())), msg=str(item.get("msg", "")), type=str(item.get("type", ""))
            )
            for item in error.errors()
        ]
        return problem_response(request, ValidationFailed("one or more fields are invalid", errors=errors))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        # no stack traces leave the process; request_id ties the log line to the response
        logger.exception("api.unhandled", path=request.url.path, request_id=_request_id(request))
        return problem_response(request, AppError())


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem, "description": HTTPStatus(status).phrase, "content": {PROBLEM_MEDIA_TYPE: {}}}
    for status in (400, 401, 403, 404, 409, 412, 422, 429, 501, 503)
}
