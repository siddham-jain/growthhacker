import re
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.errors import BadRequest
from app.api.idempotency import IdempotentRequest
from app.api.presenters import present_run, run_location
from app.api.state import ApiState
from app.pipeline.runs import RunRequestResult

CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
PAGING_PARAMS = ("cursor", "limit")


def only_params(*names: str) -> Callable[[Request], None]:
    """strict query strings: a typo in a filter is a 400, never silently ignored."""
    allowed = frozenset(names)

    def dependency(request: Request) -> None:
        unknown = sorted(set(request.query_params) - allowed)
        if unknown:
            raise BadRequest(f"unknown query parameters: {', '.join(unknown)}")

    return dependency


async def run_accepted(
    state: ApiState, result: RunRequestResult, guard: IdempotentRequest | None = None
) -> JSONResponse:
    out = await present_run(state, result.run, with_stages=True)
    headers = {"Location": run_location(result.run.id)}
    if result.deduplicated:
        headers["X-Run-Deduplicated"] = "true"
    if result.follow_up_requested:
        headers["X-Run-Follow-Up-Requested"] = "true"
    response = JSONResponse(
        out.model_dump(mode="json"), status_code=200 if result.deduplicated else 202, headers=headers
    )
    return response if guard is None else await guard.complete(response)


def json_response(model: Any, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status_code, headers=headers)
