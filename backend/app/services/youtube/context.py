from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.domain.youtube import QuotaPriority
from app.ports.youtube import RequestContext


class SearchPool(StrEnum):
    onboarding = "onboarding"
    news = "news"
    niche = "niche"


DEFAULT_SEARCH_POOL = {
    QuotaPriority.interactive: SearchPool.onboarding,
    QuotaPriority.daily_refresh: SearchPool.niche,
    QuotaPriority.snapshot: SearchPool.niche,
    QuotaPriority.background: SearchPool.niche,
}


class YouTubeRequestContext(RequestContext):
    # local extension of the port context: search sub-budget and a cache max-age override
    search_pool: SearchPool | None = None
    max_age_s: int | None = Field(default=None, ge=0)

    @property
    def pool(self) -> SearchPool:
        return self.search_pool or DEFAULT_SEARCH_POOL[self.priority]


def request_context(
    priority: QuotaPriority,
    *,
    client_id: UUID | None = None,
    run_id: UUID | None = None,
    search_pool: SearchPool | None = None,
    max_age_s: int | None = None,
) -> YouTubeRequestContext:
    return YouTubeRequestContext(
        client_id=client_id, run_id=run_id, priority=priority, search_pool=search_pool, max_age_s=max_age_s
    )


def with_max_age(ctx: RequestContext, max_age_s: int | None) -> YouTubeRequestContext:
    return YouTubeRequestContext(**{**as_youtube_context(ctx).model_dump(), "max_age_s": max_age_s})


def as_youtube_context(ctx: RequestContext) -> YouTubeRequestContext:
    if isinstance(ctx, YouTubeRequestContext):
        return ctx
    return YouTubeRequestContext(client_id=ctx.client_id, run_id=ctx.run_id, priority=ctx.priority)
