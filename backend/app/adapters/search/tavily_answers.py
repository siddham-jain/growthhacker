import time
from typing import Any
from uuid import UUID

from tavily import AsyncTavilyClient

from app.adapters.search.cache import RedisResponseCache, ResponseCache, cache_key
from app.adapters.search.ledger import CreditCharge, CreditMeter, PgUsageRecorder, UsageRecorder
from app.adapters.search.tavily import TavilyApi, TavilyWebSearch
from app.core.registry import adapter_registry
from app.services.answer_citations.models import AnswerSearchResult, AnswerSource

OPERATION = "answer_search"
BASIC_SEARCH_CREDITS = 1.0


class TavilyAnswerSearch:
    """tavily search with include_answer; cached per question and metered on the provider usage ledger."""

    def __init__(
        self,
        api: TavilyApi,
        *,
        cache: ResponseCache | None = None,
        recorder: UsageRecorder | None = None,
        cache_ttl_s: int = 0,
        request_timeout_s: float = 30,
        meter: CreditMeter | None = None,
    ) -> None:
        self._api = api
        self._cache = cache
        self._recorder = recorder
        self._cache_ttl_s = cache_ttl_s
        self._timeout_s = request_timeout_s
        self.meter = meter or CreditMeter()

    async def answer(
        self, question: str, *, max_results: int, country: str | None, client_id: UUID | None
    ) -> AnswerSearchResult:
        params: dict[str, Any] = {
            "search_depth": "basic",
            "topic": "general",
            "max_results": max_results,
            "include_answer": "basic",
            "include_usage": True,
            "timeout": self._timeout_s,
        }
        if country:
            params["country"] = country
        key = cache_key(OPERATION, {"query": question, **params})
        if self._cache is not None and self._cache_ttl_s and (cached := await self._cache.get(key)):
            await self._settle(CreditCharge(OPERATION, 0.0, 0, 0, "ok", cache_hit=True), client_id)
            return _result(cached, credits=0.0, from_cache=True)
        payload = await self._call(question, params, client_id)
        if self._cache is not None and self._cache_ttl_s:
            await self._cache.put(key, payload, self._cache_ttl_s)
        return _result(payload, credits=_credits(payload), from_cache=False)

    async def _call(self, question: str, params: dict[str, Any], client_id: UUID | None) -> dict[str, Any]:
        started = time.monotonic()
        try:
            payload = await self._api.search(question, **params)
        except Exception as error:
            failure = TavilyWebSearch._classify(error)
            await self._settle(
                CreditCharge(OPERATION, 0.0, 0, _elapsed(started), "error", error_kind=failure.kind),
                client_id,
            )
            raise failure from error
        await self._settle(CreditCharge(OPERATION, _credits(payload), 0, _elapsed(started), "ok"), client_id)
        return payload

    async def _settle(self, charge: CreditCharge, client_id: UUID | None) -> None:
        self.meter.add(charge)
        if self._recorder is not None:
            await self._recorder.record(charge, client_id=client_id, run_id=None)


def _credits(payload: dict[str, Any]) -> float:
    usage = payload.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("credits"), int | float):
        return float(usage["credits"])
    return BASIC_SEARCH_CREDITS


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _result(payload: dict[str, Any], *, credits: float, from_cache: bool) -> AnswerSearchResult:
    urls = [item.get("url") for item in payload.get("results", []) if isinstance(item, dict)]
    answer = payload.get("answer")
    return AnswerSearchResult(
        answer=answer if isinstance(answer, str) and answer.strip() else None,
        sources=[AnswerSource(url=url, position=rank) for rank, url in enumerate(urls, start=1) if url],
        credits_used=credits,
        from_cache=from_cache,
    )


@adapter_registry.register("answer_search", "tavily")
def build_tavily_answer_search(container: Any) -> TavilyAnswerSearch:
    settings = container.settings
    if settings.tavily.api_key is None:
        raise LookupError("TAVILY_API_KEY is not configured")
    return TavilyAnswerSearch(
        AsyncTavilyClient(api_key=settings.tavily.api_key.get_secret_value()),
        cache=RedisResponseCache(container.redis, prefix=settings.redis.key_prefix),
        recorder=PgUsageRecorder(container.session_factory, container.clock, stage="answer_citations"),
        cache_ttl_s=settings.answer_citations.search_cache_ttl_s,
        request_timeout_s=settings.tavily.request_timeout_s,
    )
