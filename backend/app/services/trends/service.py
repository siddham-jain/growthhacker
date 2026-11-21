import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core.config import TrendSettings
from app.domain.topics import TopicRef
from app.domain.trends import KeywordTrend, TrendIdea, TrendScan
from app.ports.search import WebSearch, WebSearchHit, WebSearchQuery
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.news.text import normalize
from app.services.trends.candidates import CandidateBuilder, searchable_seeds
from app.services.trends.heat import score
from app.services.trends.ideas import accept_ideas
from app.services.trends.measure import KeywordMeasurer
from app.services.trends.writer import ClientBrief, TrendWriter

logger = logging.getLogger(__name__)

CONCURRENCY = 4
SEEDS_PER_WEB_QUERY = 4


class Suggester(Protocol):
    async def suggest(self, seed: str, *, region: str | None, language: str | None) -> list[str]: ...


@dataclass(frozen=True)
class TrendRequest:
    brief: ClientBrief
    refs: Sequence[TopicRef]
    youtube: RequestContext
    brand_terms: Sequence[str]
    region: str | None
    language: str | None
    competitors: set[str]
    window_days: int
    keyword_budget: int
    previous: TrendScan | None
    now: datetime


class TrendScanService:
    """client keywords + youtube suggestions + web news -> measured recent uploads -> heat -> grounded ideas."""

    def __init__(
        self,
        *,
        settings: TrendSettings,
        gateway: YouTubeGateway,
        suggester: Suggester | None,
        web: WebSearch | None,
        writer: TrendWriter | None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._suggester = suggester if settings.autocomplete_enabled else None
        self._web = web if settings.web_keywords_enabled else None
        self._writer = writer

    async def scan(self, request: TrendRequest) -> TrendScan:
        cfg = self._settings
        messages: list[str] = []
        seeds = searchable_seeds(
            request.refs, cfg.seed_topics, day=request.now.date().toordinal(), brand_terms=request.brand_terms
        )
        if not seeds:
            return TrendScan(
                as_of=request.now,
                window_days=request.window_days,
                messages=["Add a few keywords or offerings to your profile so we can look for trends."],
            )
        builder = CandidateBuilder(request.refs)
        for ref in seeds:
            builder.add_seed(ref)
        await self._add_suggestions(builder, seeds, request)
        credits = await self._add_web_keywords(builder, seeds, request, messages)
        measurer = KeywordMeasurer(
            gateway=self._gateway, window_days=request.window_days, videos_kept=cfg.videos_per_keyword
        )
        checked = await self._measure(measurer, builder, request)
        if any(trend.status == "unchecked_quota" for trend in checked):
            messages.append("Some keywords were not checked today because of the daily YouTube search limit.")
        checked = [trend for trend in checked if trend.status not in ("unchecked_quota", "unchecked_error")]
        trends = score(
            [*checked, *self._carried(checked, request)],
            request.previous,
            hot_ratio=cfg.hot_ratio,
            min_videos=cfg.min_videos,
            min_channels=cfg.min_channels,
        )
        ideas, attempted = await self._ideas(trends, request)
        if attempted and not ideas:
            messages.append("Ideas could not be written right now; the trending videos are below.")
        return TrendScan(
            as_of=request.now,
            window_days=request.window_days,
            region=request.region,
            language=request.language,
            keywords=trends,
            ideas=ideas,
            search_calls=sum(trend.status == "checked" for trend in checked),
            llm_calls=self._writer.calls if self._writer else 0,
            tavily_credits=credits,
            messages=messages,
        )

    async def _add_suggestions(
        self, builder: CandidateBuilder, seeds: Sequence[TopicRef], request: TrendRequest
    ) -> None:
        if self._suggester is None or self._settings.suggestions_per_seed <= 0:
            return
        results = await asyncio.gather(
            *(
                self._suggester.suggest(ref.label, region=request.region, language=request.language)
                for ref in seeds
            )
        )
        for ref, suggestions in zip(seeds, results, strict=True):
            builder.add_suggestions(ref, suggestions, self._settings.suggestions_per_seed)

    async def _add_web_keywords(
        self, builder: CandidateBuilder, seeds: Sequence[TopicRef], request: TrendRequest, messages: list[str]
    ) -> float:
        cfg = self._settings
        if self._web is None or self._writer is None or cfg.web_queries <= 0 or cfg.web_keywords <= 0:
            return 0.0
        groups = [seeds[i : i + SEEDS_PER_WEB_QUERY] for i in range(0, len(seeds), SEEDS_PER_WEB_QUERY)]
        queries = [
            WebSearchQuery(
                query=" OR ".join(ref.label for ref in group) + " latest news",
                topic="news",
                time_range="week",
                max_results=cfg.web_results_per_query,
            )
            for group in groups[: cfg.web_queries]
        ]
        hits: list[WebSearchHit] = []
        credits = 0.0
        for query in queries:
            try:
                response = await self._web.search(query)
            except Exception:
                logger.exception("trend web search failed", extra={"query": query.query})
                continue
            credits += response.credits_used
            hits.extend(response.hits)
        unique = list({hit.url: hit for hit in hits}.values())
        keywords = await self._writer.web_keywords(
            request.brief, seeds, unique, cfg.web_keywords, request.now
        )
        builder.add_web(keywords)
        if queries and not unique:
            messages.append("No recent news found for your topics this week.")
        return credits

    async def _measure(
        self, measurer: KeywordMeasurer, builder: CandidateBuilder, request: TrendRequest
    ) -> list[KeywordTrend]:
        refs = {ref.id: ref for ref in request.refs}
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(candidate):
            async with gate:
                return await measurer.measure(
                    candidate,
                    ref=refs.get(candidate.topic_ref_id),
                    ctx=request.youtube,
                    region=request.region,
                    language=request.language,
                    competitors=request.competitors,
                    now=request.now,
                )

        return list(await asyncio.gather(*(one(c) for c in builder.ranked(request.keyword_budget))))

    def _carried(self, checked: Sequence[KeywordTrend], request: TrendRequest) -> list[KeywordTrend]:
        """earlier checks with videos, not repeated today, stay visible until carry_over_days with their own checked_at."""
        if request.previous is None:
            return []
        fresh = {normalize(trend.candidate.keyword) for trend in checked}
        live_topics = {ref.id for ref in request.refs}
        oldest = request.now - timedelta(days=self._settings.carry_over_days)
        return [
            trend
            for trend in request.previous.keywords
            if normalize(trend.candidate.keyword) not in fresh
            and trend.candidate.topic_ref_id in live_topics
            and trend.checked_at >= oldest
            and trend.recent_videos
        ]

    async def _ideas(
        self, trends: Sequence[KeywordTrend], request: TrendRequest
    ) -> tuple[list[TrendIdea], bool]:
        cfg = self._settings
        with_videos = [trend for trend in trends if len(trend.videos) >= cfg.idea_min_references]
        if self._writer is None or not with_videos:
            return [], False
        picked = [t for t in with_videos if t.heat != "quiet"] or with_videos
        picked = picked[: cfg.ideas_keywords]
        drafts = await self._writer.ideas(request.brief, picked, cfg.ideas_per_keyword, request.now)
        return accept_ideas(
            drafts,
            picked,
            buyer_questions=request.brief.buyer_questions,
            own_titles=request.brief.own_titles,
            min_references=cfg.idea_min_references,
            per_keyword=cfg.ideas_per_keyword,
        ), True
