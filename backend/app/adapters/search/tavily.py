import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from uuid import UUID

from tavily import AsyncTavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from app.adapters.search.cache import RedisResponseCache, ResponseCache, cache_key
from app.adapters.search.errors import WebSearchError
from app.adapters.search.ledger import CreditCharge, CreditMeter, PgUsageRecorder, UsageRecorder
from app.core.registry import adapter_registry
from app.ports.clock import Clock
from app.ports.search import (
    ExtractedDocument,
    ExtractRequest,
    ExtractResponse,
    FailedExtraction,
    SiteMapRequest,
    SiteMapResponse,
    WebSearchHit,
    WebSearchQuery,
    WebSearchResponse,
)

EXTRACT_URLS_PER_CREDIT = 5
MAP_PAGES_PER_CREDIT = 10


class TavilyApi(Protocol):
    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...

    async def extract(self, urls: list[str], **kwargs: Any) -> dict[str, Any]: ...

    async def map(self, url: str, **kwargs: Any) -> dict[str, Any]: ...

    async def crawl(self, url: str, **kwargs: Any) -> dict[str, Any]: ...


def _published(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _credits(payload: dict[str, Any], estimate: float) -> float:
    usage = payload.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("credits"), int | float):
        return float(usage["credits"])
    return estimate


def _extract_estimate(succeeded: int, depth: str) -> float:
    per_batch = 2 if depth == "advanced" else 1
    return per_batch * -(-succeeded // EXTRACT_URLS_PER_CREDIT)


class TavilyWebSearch:
    """the shared WebSearch adapter (99 G1): search, extract, map and crawl with caching and metering."""

    def __init__(
        self,
        api: TavilyApi,
        *,
        clock: Clock,
        cache: ResponseCache | None = None,
        recorder: UsageRecorder | None = None,
        cache_ttl_s: int = 86_400,
        request_timeout_s: float = 30,
        client_id: UUID | None = None,
        run_id: UUID | None = None,
        meter: CreditMeter | None = None,
    ) -> None:
        self._api = api
        self._clock = clock
        self._cache = cache
        self._recorder = recorder
        self._cache_ttl_s = cache_ttl_s
        self._timeout_s = request_timeout_s
        self._client_id = client_id
        self._run_id = run_id
        self.meter = meter or CreditMeter()

    def with_attribution(self, *, client_id: UUID | None, run_id: UUID | None) -> "TavilyWebSearch":
        return TavilyWebSearch(
            self._api,
            clock=self._clock,
            cache=self._cache,
            recorder=self._recorder,
            cache_ttl_s=self._cache_ttl_s,
            request_timeout_s=self._timeout_s,
            client_id=client_id,
            run_id=run_id,
        )

    async def search(self, query: WebSearchQuery) -> WebSearchResponse:
        params: dict[str, Any] = {
            "search_depth": query.search_depth,
            "topic": query.topic,
            "max_results": query.max_results,
            "include_usage": True,
            "timeout": self._timeout_s,
        }
        if query.time_range:
            params["time_range"] = query.time_range
        if query.include_domains:
            params["include_domains"] = query.include_domains
        if query.exclude_domains:
            params["exclude_domains"] = query.exclude_domains
        if query.include_raw_content:
            params["include_raw_content"] = "markdown"
        if query.country and query.topic == "general":
            params["country"] = query.country
        payload, from_cache = await self._cached(
            "search",
            {"query": query.query, **params},
            lambda: self._api.search(query.query, **params),
            estimate=lambda _: 2.0 if query.search_depth == "advanced" else 1.0,
        )
        hits = [
            WebSearchHit(
                url=item["url"],
                title=item.get("title") or "",
                snippet=item.get("content") or "",
                provider_score=item.get("score"),
                published_at=_published(item.get("published_date")),
                raw_content=item.get("raw_content"),
            )
            for item in payload.get("results", [])
            if item.get("url")
        ]
        return WebSearchResponse(
            query=query,
            hits=hits,
            credits_used=0.0 if from_cache else _credits(payload, 1.0),
            from_cache=from_cache,
            fetched_at=self._fetched_at(payload),
        )

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        params: dict[str, Any] = {
            "extract_depth": request.depth,
            "format": request.format,
            "include_usage": True,
            "timeout": min(60.0, max(self._timeout_s, 30.0 if request.depth == "advanced" else 10.0)),
        }
        if request.query:
            params["query"] = request.query
        payload = await self._call(
            "extract",
            lambda: self._api.extract(list(request.urls), **params),
            estimate=lambda body: _extract_estimate(len(body.get("results", [])), request.depth),
            pages=lambda body: len(body.get("results", [])),
        )
        return ExtractResponse(
            results=[
                ExtractedDocument(
                    url=item["url"],
                    raw_content=item.get("raw_content") or "",
                    published_at=_published(item.get("published_date")),
                )
                for item in payload.get("results", [])
                if item.get("url")
            ],
            failed=[
                FailedExtraction(url=item.get("url", ""), error=str(item.get("error", "")))
                for item in payload.get("failed_results", [])
            ],
            credits_used=_credits(payload, _extract_estimate(len(payload.get("results", [])), request.depth)),
            fetched_at=self._clock.now(),
        )

    async def map(self, request: SiteMapRequest) -> SiteMapResponse:
        params = self._site_params(request)
        payload, from_cache = await self._cached(
            "map",
            {"url": request.url, **params},
            lambda: self._api.map(request.url, **params),
            estimate=lambda body: float(-(-len(body.get("results", [])) // MAP_PAGES_PER_CREDIT)),
        )
        urls = [item if isinstance(item, str) else item.get("url", "") for item in payload.get("results", [])]
        return SiteMapResponse(
            base_url=payload.get("base_url") or request.url,
            urls=[url for url in urls if url],
            credits_used=0.0 if from_cache else _credits(payload, len(urls) / MAP_PAGES_PER_CREDIT),
            fetched_at=self._fetched_at(payload),
        )

    async def crawl(self, request: SiteMapRequest) -> ExtractResponse:
        params = {**self._site_params(request), "format": "markdown", "extract_depth": "basic"}

        def estimate(body: dict[str, Any]) -> float:
            found = len(body.get("results", []))
            return found / MAP_PAGES_PER_CREDIT + _extract_estimate(found, "basic")

        payload = await self._call(
            "crawl",
            lambda: self._api.crawl(request.url, **params),
            estimate=estimate,
            pages=lambda body: len(body.get("results", [])),
        )
        return ExtractResponse(
            results=[
                ExtractedDocument(url=item["url"], raw_content=item.get("raw_content") or "")
                for item in payload.get("results", [])
                if item.get("url")
            ],
            credits_used=_credits(payload, estimate(payload)),
            fetched_at=self._clock.now(),
        )

    def _site_params(self, request: SiteMapRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "max_depth": request.max_depth,
            "max_breadth": request.max_breadth,
            "limit": request.limit,
            "allow_external": False,
            "include_usage": True,
        }
        if request.instructions:
            params["instructions"] = request.instructions
        if request.select_paths:
            params["select_paths"] = request.select_paths
        if request.exclude_paths:
            params["exclude_paths"] = request.exclude_paths
        return params

    def _fetched_at(self, payload: dict[str, Any]) -> datetime:
        stamp = payload.get("_fetched_at")
        return datetime.fromisoformat(stamp) if isinstance(stamp, str) else self._clock.now()

    async def _cached(
        self,
        operation: str,
        params: dict[str, Any],
        call: Callable[[], Awaitable[dict[str, Any]]],
        *,
        estimate: Callable[[dict[str, Any]], float],
    ) -> tuple[dict[str, Any], bool]:
        key = cache_key(operation, params)
        if self._cache is not None and (cached := await self._cache.get(key)) is not None:
            await self._settle(CreditCharge(operation, 0.0, 0, 0, "ok", cache_hit=True))
            return cached, True
        payload = await self._call(operation, call, estimate=estimate, pages=lambda body: 0)
        if self._cache is not None:
            await self._cache.put(
                key, {**payload, "_fetched_at": self._clock.now().isoformat()}, self._cache_ttl_s
            )
        return payload, False

    async def _call(
        self,
        operation: str,
        call: Callable[[], Awaitable[dict[str, Any]]],
        *,
        estimate: Callable[[dict[str, Any]], float],
        pages: Callable[[dict[str, Any]], int],
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            payload = await call()
        except Exception as error:
            failure = self._classify(error)
            await self._settle(
                CreditCharge(operation, 0.0, 0, self._elapsed(started), "error", error_kind=failure.kind)
            )
            raise failure from error
        await self._settle(
            CreditCharge(
                operation, _credits(payload, estimate(payload)), pages(payload), self._elapsed(started), "ok"
            )
        )
        return payload

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _classify(error: Exception) -> WebSearchError:
        if isinstance(error, UsageLimitExceededError):
            return WebSearchError("credit_limit", str(error))
        if isinstance(error, InvalidAPIKeyError | MissingAPIKeyError | ForbiddenError):
            return WebSearchError("auth", type(error).__name__)
        if isinstance(error, BadRequestError):
            return WebSearchError("bad_request", str(error))
        if isinstance(error, TavilyTimeoutError | TimeoutError):
            return WebSearchError("timeout", str(error))
        if "429" in str(error) or "rate limit" in str(error).lower():
            return WebSearchError("rate_limited", str(error))
        return WebSearchError("unavailable", f"{type(error).__name__}: {error}")

    async def _settle(self, charge: CreditCharge) -> None:
        self.meter.add(charge)
        if self._recorder is not None:
            await self._recorder.record(charge, client_id=self._client_id, run_id=self._run_id)


@adapter_registry.register("web_search", "tavily")
def build_tavily_web_search(container: Any) -> TavilyWebSearch:
    settings = container.settings
    if settings.tavily.api_key is None:
        raise LookupError("TAVILY_API_KEY is not configured")
    api = AsyncTavilyClient(api_key=settings.tavily.api_key.get_secret_value())
    return TavilyWebSearch(
        api,
        clock=container.clock,
        cache=RedisResponseCache(container.redis, prefix=settings.redis.key_prefix),
        recorder=PgUsageRecorder(container.session_factory, container.clock),
        cache_ttl_s=settings.website_intel.search_cache_ttl_s,
        request_timeout_s=settings.tavily.request_timeout_s,
    )
