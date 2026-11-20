from pydantic import BaseModel

from app.core.logging import get_logger
from app.domain.web import FetchResult
from app.pipeline.contracts import BudgetExceeded
from app.ports.crawler import SiteCrawler
from app.ports.llm import (
    LLMClient,
    LLMError,
    LLMErrorKind,
    LLMUnavailable,
    PromptInput,
    PromptRef,
    TaskId,
    Tier,
)
from app.ports.search import WebSearch, WebSearchQuery, WebSearchResponse
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.payloads import PAYLOADS_BY_TASK

log = get_logger(__name__)
LLM_OUTAGE_KINDS = frozenset(
    {LLMErrorKind.circuit_open, LLMErrorKind.disabled, LLMErrorKind.auth, LLMErrorKind.billing}
)
PROMPT_VERSION = "active"


async def search_web(
    ctx: DiscoveryContext, client: WebSearch | None, query: WebSearchQuery
) -> WebSearchResponse | None:
    if client is None or "web_search" in ctx.degraded:
        return None
    credits = 2.0 if query.search_depth == "advanced" else 1.0
    try:
        ctx.meter.reserve_tavily(credits)
    except BudgetExceeded:
        ctx.warn(
            "TAVILY_BUDGET_REACHED", "Web search budget for this scan is used up; some checks were skipped."
        )
        return None
    try:
        response = await client.search(query)
    except Exception as error:  # a provider outage degrades only the web path (edge case 21)
        ctx.meter.settle_tavily(credits, 0.0)
        log.warning("competitors.web_search_failed", error=type(error).__name__)
        ctx.degraded.add("web_search")
        ctx.warn(
            "WEB_SEARCH_UNAVAILABLE",
            "Web search is unavailable, so suggestions come from AI and YouTube only. Please review them.",
        )
        return None
    ctx.meter.settle_tavily(credits, response.credits_used)
    return response


async def structured[T: BaseModel](
    ctx: DiscoveryContext,
    client: LLMClient | None,
    *,
    task: TaskId,
    schema: type[T],
    prompt_input: PromptInput,
    tier: Tier,
    stage: str = "discover_competitors",
) -> T | None:
    if client is None or "llm" in ctx.degraded:
        return None
    try:
        ctx.meter.ensure_llm()
    except BudgetExceeded:
        ctx.warn("LLM_BUDGET_REACHED", "AI budget for this scan is used up; remaining steps used rules only.")
        return None
    try:
        result = await client.generate_structured(
            task=task,
            schema=schema,
            system=PromptRef(prompt_id=task.value, version=PROMPT_VERSION),
            input=prompt_input,
            context=ctx.call_context(stage, *PAYLOADS_BY_TASK[task]),
            tier=tier,
        )
    except LLMError as error:
        log.warning("competitors.llm_failed", task=task.value, kind=error.kind.value)
        if isinstance(error, LLMUnavailable) or error.kind in LLM_OUTAGE_KINDS:
            ctx.degraded.add("llm")
            ctx.warn("LLM_UNAVAILABLE", "AI classification unavailable. Please check suggested competitors.")
        return None
    ctx.meter.charge_llm(result)
    return result.value


async def fetch_page(ctx: DiscoveryContext, crawler: SiteCrawler | None, url: str) -> FetchResult | None:
    if crawler is None:
        return None
    try:
        return await crawler.fetch_page(url, ctx.fetch_context)
    except Exception as error:  # an unreachable competitor site only removes one zero-cost resolver
        log.info("competitors.fetch_failed", url=url, error=type(error).__name__)
        return None
