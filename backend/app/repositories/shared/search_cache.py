from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.youtube_cache import YtSearchCache, YtSearchResult
from app.repositories.shared.base import SharedRepository

MAX_SEARCH_CACHE_TTL = timedelta(days=28)


@dataclass(frozen=True)
class SearchResultItem:
    kind: str
    resource_id: str
    channel_id: str | None = None


@dataclass(frozen=True)
class CachedSearch:
    query_key: str
    params: dict[str, Any]
    items: list[SearchResultItem]
    fetched_at: datetime
    expires_at: datetime
    total_results: int | None


class SearchCacheRepository(SharedRepository):
    async def get_fresh(self, query_key: str, *, now: datetime) -> CachedSearch | None:
        cache = (
            await self.session.execute(
                select(YtSearchCache).where(
                    YtSearchCache.query_key == query_key, YtSearchCache.expires_at > now
                )
            )
        ).scalar_one_or_none()
        if cache is None:
            return None
        await self.session.execute(
            update(YtSearchCache)
            .where(YtSearchCache.query_key == query_key)
            .values(hit_count=YtSearchCache.hit_count + 1, last_hit_at=now)
        )
        results = await self.session.execute(
            select(YtSearchResult).where(YtSearchResult.query_key == query_key).order_by(YtSearchResult.rank)
        )
        items = [SearchResultItem(row.kind, row.resource_id, row.channel_id) for row in results.scalars()]
        return CachedSearch(
            query_key=cache.query_key,
            params=cache.params,
            items=items,
            fetched_at=cache.fetched_at,
            expires_at=cache.expires_at,
            total_results=cache.total_results,
        )

    async def put(
        self,
        *,
        query_key: str,
        endpoint: str,
        params: Mapping[str, Any],
        items: Sequence[SearchResultItem],
        fetched_at: datetime,
        ttl: timedelta,
        quota_units: int,
        total_results: int | None = None,
        next_page_token: str | None = None,
    ) -> bool:
        if ttl <= timedelta(0) or ttl > MAX_SEARCH_CACHE_TTL:
            raise ValueError(f"search cache ttl must be within (0, {MAX_SEARCH_CACHE_TTL}]")
        values = {
            "query_key": query_key,
            "endpoint": endpoint,
            "params": dict(params),
            "total_results": total_results,
            "next_page_token": next_page_token,
            "quota_units": quota_units,
            "fetched_at": fetched_at,
            "expires_at": fetched_at + ttl,
        }
        statement = insert(YtSearchCache).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["query_key"],
            set_={name: statement.excluded[name] for name in values if name != "query_key"},
            where=statement.excluded.fetched_at >= YtSearchCache.fetched_at,
        )
        if (await self.session.execute(statement)).rowcount == 0:
            return False
        await self.session.execute(delete(YtSearchResult).where(YtSearchResult.query_key == query_key))
        if items:
            await self.session.execute(
                insert(YtSearchResult).values(
                    [
                        {
                            "query_key": query_key,
                            "rank": rank,
                            "kind": item.kind,
                            "resource_id": item.resource_id,
                            "channel_id": item.channel_id,
                        }
                        for rank, item in enumerate(items)
                    ]
                )
            )
        return True
