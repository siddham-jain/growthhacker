from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.auth import Authenticator
from app.api.deps import access
from app.api.errors import install_error_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.api.pagination import Cursors
from app.api.presenters import install_pipeline_error_handlers
from app.api.ratelimit import ApiRateLimiter
from app.api.signing import Signer
from app.api.sse import PubSubHub
from app.api.state import ApiState
from app.api.v1 import admin, answer_citations, auth, clients, events, health, results, runs, setup
from app.api.v1 import settings as settings_routes
from app.bootstrap import load_registrations
from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.core.logging import configure_logging, get_logger
from app.pipeline.registry import PipelineCatalog
from app.pipeline.wiring import build_pipeline_services

logger = get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "If-Match",
    "If-None-Match",
    "Last-Event-ID",
    "X-Request-ID",
    "X-Requested-With",
]
CORS_EXPOSE = [
    "Location",
    "ETag",
    "Retry-After",
    "RateLimit",
    "RateLimit-Policy",
    "X-Request-ID",
    "X-Run-Deduplicated",
    "X-Run-Follow-Up-Requested",
    "Idempotent-Replayed",
    "Deprecation",
    "Sunset",
]


def alembic_heads() -> frozenset[str]:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def _job_queue(container: Container):
    try:
        return container.adapter("job_queue")
    except LookupError as error:
        logger.warning("api.job_queue_unavailable", error=str(error))
        return None


def build_state(container: Container, catalog: PipelineCatalog | None = None) -> ApiState:
    settings = container.settings
    job_queue = _job_queue(container)
    signer = Signer.from_settings(settings)
    return ApiState(
        container=container,
        pipeline=build_pipeline_services(container, job_queue=job_queue, catalog=catalog),
        authenticator=Authenticator(container.session_factory, settings, container.passwords),
        signer=signer,
        cursors=Cursors(signer),
        hub=PubSubHub(
            redis_url=settings.redis.url.get_secret_value(),
            prefix=settings.redis.key_prefix,
            max_connections=settings.api.sse_max_connections,
        ),
        limiter=ApiRateLimiter(
            container.redis, prefix=settings.redis.key_prefix, settings=settings.api.rate_limits
        ),
        job_queue=job_queue,
        alembic_heads=alembic_heads(),
    )


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container is not None else get_settings())
    owns_container = container is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.app)
        report = load_registrations()
        if report.missing:
            logger.info("api.registrations_missing", modules=report.missing)
        active = container or build_container(settings)
        state = build_state(active)
        await state.hub.start()
        app.state.api = state
        try:
            yield
        finally:
            state.shutdown.set()
            await state.hub.stop()
            if owns_container:
                await active.aclose()

    docs = settings.api.docs_enabled and settings.app.env != "prod"
    app = FastAPI(
        title="GrowthHacker API",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs" if docs else None,
        redoc_url=None,
        generate_unique_id_function=lambda route: route.name,
    )
    install_error_handlers(app)
    install_pipeline_error_handlers(app)
    # added innermost first: requests pass request-context -> cors -> body limit -> gzip -> routes
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.api.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        expose_headers=CORS_EXPOSE,
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(_v1())
    return app


def _v1() -> APIRouter:
    v1 = APIRouter(prefix="/api/v1")
    routers = (
        clients.router,
        setup.router,
        runs.router,
        results.router,
        admin.router,
        settings_routes.router,
        answer_citations.router,
    )
    for router in routers:
        v1.include_router(router, dependencies=[Depends(access)])
    for router in (auth.router, events.router, admin.public_router):
        v1.include_router(router)
    return v1
