import hashlib
import json
import unicodedata
from datetime import date, datetime, timedelta

import regex

from app.core.config import YouTubeFreshnessSettings
from app.domain.youtube import SearchPage, SearchQuery, VideoRecord
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.youtube.context import SearchPool, as_youtube_context

PUNCTUATION = regex.compile(r'[^\p{L}\p{N}\s"|-]+')
WHITESPACE = regex.compile(r"\s+")


def normalize_query_text(q: str) -> str:
    text = unicodedata.normalize("NFKC", q).casefold()
    text = PUNCTUATION.sub(" ", text)
    alternatives = [WHITESPACE.sub(" ", part).strip() for part in text.split("|")]
    alternatives = sorted({part for part in alternatives if part})
    return " | ".join(alternatives)


def search_params_for_key(query: SearchQuery) -> dict[str, str | int | None]:
    return {
        "q": normalize_query_text(query.q),
        "type": query.type,
        "order": query.order,
        "published_after": query.published_after.isoformat() if query.published_after else None,
        "published_before": query.published_before.isoformat() if query.published_before else None,
        "region_code": query.region_code,
        "relevance_language": query.relevance_language,
        "video_duration": query.video_duration,
        "page": query.page,
    }


def search_key(query: SearchQuery) -> str:
    # dates are already day-rounded, so every client in the same niche shares a key on the same day
    payload = json.dumps(search_params_for_key(query), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def search_ttl(
    query: SearchQuery, pool: SearchPool, settings: YouTubeFreshnessSettings, today: date
) -> timedelta:
    if pool is SearchPool.news:
        return timedelta(hours=settings.search_news_h)
    if query.published_after is None:
        return timedelta(hours=settings.search_long_h)
    span = (query.published_before or today) - query.published_after
    if span <= timedelta(days=7):
        return timedelta(hours=settings.search_week_h)
    if span <= timedelta(days=30):
        return timedelta(hours=settings.search_month_h)
    return timedelta(hours=settings.search_long_h)


class CachedSearchService:
    """cached niche search; ranks are api list positions and may differ from what users see."""

    def __init__(self, gateway: YouTubeGateway) -> None:
        self._gateway = gateway

    async def niche_search(
        self, query: SearchQuery, ctx: RequestContext
    ) -> tuple[SearchPage, list[VideoRecord]]:
        page = await self._gateway.search(query, None, ctx)
        video_ids = list(dict.fromkeys(hit.id for hit in page.items if hit.kind == "video"))
        if not video_ids:
            return page, []
        batch = await self._gateway.videos_by_id(video_ids, as_youtube_context(ctx))
        by_id = {video.video_id: video for video in batch.found}
        hydrated = [
            by_id[video_id].model_copy(update={"ingest_source": "search"})
            for video_id in video_ids
            if video_id in by_id and by_id[video_id].privacy_status == "public"
        ]
        return page, hydrated


def published_bound(day: date | None) -> str | None:
    if day is None:
        return None
    return datetime(day.year, day.month, day.day).strftime("%Y-%m-%dT00:00:00Z")
