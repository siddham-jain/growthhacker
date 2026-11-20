from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import State

router = APIRouter(tags=["health"])


@router.get("/healthz", operation_id="health_live", include_in_schema=True)
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", operation_id="health_ready", responses={503: {"description": "not ready"}})
async def health_ready(request: Request, state: State) -> JSONResponse:
    # unauthenticated, so it reports only ok / not ready, never which dependency failed
    ready = await _database_ready(state) and await _redis_ready(state)
    return JSONResponse({"status": "ok" if ready else "unavailable"}, status_code=200 if ready else 503)


async def _database_ready(state: State) -> bool:
    try:
        async with state.container.session_factory() as session:
            versions = set(
                (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
            )
    except (SQLAlchemyError, OSError):
        return False
    return versions == set(state.alembic_heads)


async def _redis_ready(state: State) -> bool:
    try:
        return bool(await state.container.redis.ping())
    except (RedisError, OSError):
        return False
