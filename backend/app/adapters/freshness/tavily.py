from datetime import datetime
from typing import Any, ClassVar, Literal

import httpx
from tavily import AsyncTavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from app.adapters.freshness.base import FreshnessDeps, clean_text, parse_iso, parse_rfc1123
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

ALWAYS_EXCLUDED_DOMAINS = (
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
)
BASIC_SEARCH_CREDITS = 1.0


class TavilySearchSource:
    key: ClassVar[str]
    capabilities: ClassVar[SourceCapabilities]
    topic: ClassVar[Literal["news", "general"]]

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps
        api_key = deps.settings.tavily.api_key
        self._api_key = api_key.get_secret_value() if api_key is not None else None

    def _request(self, query: PlannedQuery) -> dict[str, Any]:
        params = query.params
        excluded = sorted(set(params.get("exclude_domains", [])) | set(ALWAYS_EXCLUDED_DOMAINS))
        request: dict[str, Any] = {
            "query": query.normalized_query,
            "topic": self.topic,
            "search_depth": "basic",
            "max_results": params.get("max_results", self.deps.settings.news.max_results_per_query),
            "time_range": params.get("time_range", "week"),
            "include_usage": True,
            "include_raw_content": False,
            "auto_parameters": False,
            "exclude_domains": excluded,
            "filter_by_published_date": True,
            "timeout": self.deps.settings.tavily.request_timeout_s,
        }
        for optional in ("language", "filter_by_language"):
            if optional in params:
                request[optional] = params[optional]
        return request

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        if self._api_key is None:
            return NewsFetchResult(items=[], cost=0.0, status="error", error="tavily api key missing")
        client = AsyncTavilyClient(api_key=self._api_key)
        try:
            response = await client.search(**self._request(query))
        except UsageLimitExceededError as error:
            return _failure("rate_limited", error)
        except ForbiddenError as error:
            return _failure("exhausted", error)
        except (InvalidAPIKeyError, MissingAPIKeyError, BadRequestError, TavilyTimeoutError) as error:
            return _failure("error", error)
        except httpx.HTTPError as error:
            return _failure("error", error)
        finally:
            await client.close()
        return NewsFetchResult(
            items=[_to_item(result) for result in response.get("results", []) if result.get("url")],
            cost=float((response.get("usage") or {}).get("credits") or BASIC_SEARCH_CREDITS),
            status="ok",
            provider_request_id=response.get("request_id"),
        )

    async def health(self) -> SourceHealth:
        healthy = self._api_key is not None
        return SourceHealth(
            healthy=healthy,
            detail=None if healthy else "tavily api key missing",
            checked_at=self.deps.clock.now(),
        )


@freshness_source_registry.register("tavily_news")
class TavilyNewsSource(TavilySearchSource):
    key = "tavily_news"
    topic = "news"
    capabilities = SourceCapabilities(
        supports_time_filter=True,
        supports_language=True,
        supports_region=False,
        returns_dates="mixed",
        cost_unit="tavily_credit",
        min_interval_seconds=0.0,
        content_policy="snippet_ok",
    )


@freshness_source_registry.register("tavily_domains")
class TavilyDomainSource(TavilySearchSource):
    key = "tavily_domains"
    topic = "general"
    capabilities = SourceCapabilities(
        supports_time_filter=True,
        supports_language=True,
        supports_region=True,
        returns_dates="mixed",
        cost_unit="tavily_credit",
        min_interval_seconds=0.0,
        content_policy="snippet_ok",
    )

    def _request(self, query: PlannedQuery) -> dict[str, Any]:
        request = super()._request(query)
        request["include_domains"] = list(query.params.get("include_domains", []))
        request["include_domains_mode"] = "restrict"
        if "country" in query.params:
            request["country"] = query.params["country"]
        return request


def _failure(status: Literal["rate_limited", "exhausted", "error"], error: Exception) -> NewsFetchResult:
    return NewsFetchResult(items=[], cost=0.0, status=status, error=str(error)[:300] or type(error).__name__)


def _to_item(result: dict[str, Any]) -> RawNewsItem:
    published = result.get("published_date")
    return RawNewsItem(
        url=result["url"],
        title=clean_text(result.get("title"), 500),
        snippet=clean_text(result.get("content")),
        published_at=parse_rfc1123(published) or parse_iso(published),
        provider_score=result.get("score"),
    )
