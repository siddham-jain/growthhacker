from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from pydantic import AwareDatetime
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.db import UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.common import DomainModel
from app.domain.news import NewsItem
from app.domain.recommendations import RecommendationDraft
from app.ports.embeddings import Embedder, EmbedderUnavailable
from app.services.news.clustering import StoryClusterer
from app.services.news.context import NewsClientContext
from app.services.news.gates import surface_of
from app.services.news.opportunity import SURFACE_EARLY_SIGNALS, SURFACE_RECOMMENDATIONS, SURFACE_REVIEW
from app.services.news.packs import SourcePackRegistry
from app.services.news.poller import NewsPoller
from app.services.news.prefilter import StoryBundle
from app.services.news.query_planner import QueryPlanner
from app.services.news.repository import (
    NewsItemStore,
    NewsQueryStore,
    NewsSignalStore,
)
from app.services.news.scanner import NewsScanner, ScanInputs, ScanOutcome

POLL_BATCH = 200
CLUSTER_WINDOW = timedelta(days=30)
LIFECYCLE_WINDOW = timedelta(days=45)
QUIET_LEVELS = frozenset({"low", "none"})


class NewsWithdrawal(DomainModel):
    signal_id: UUID
    verification_status: Literal["active", "disputed", "retracted", "superseded"]
    status: str
    reason: str
    recommendation_ids: list[str]
    refresh_keywords: list[str]


class NewsRadarReport(DomainModel):
    """the news stage artifact: drafts per surface plus lifecycle changes for 07 and 09."""

    client_id: UUID
    as_of: AwareDatetime
    profile_version: int
    level: str
    recommendation_drafts: list[RecommendationDraft] = []
    early_signal_drafts: list[RecommendationDraft] = []
    review_drafts: list[RecommendationDraft] = []
    signal_ids: list[UUID] = []
    refresh_signal_ids: list[UUID] = []
    withdrawals: list[NewsWithdrawal] = []
    suppressed: dict[str, int] = {}
    gate_suppressed: dict[str, int] = {}
    queries_planned: int = 0
    queries_dropped: int = 0
    tavily_credits: float = 0.0
    llm_calls: int = 0
    degraded: bool = False
    message: str | None = None

    @property
    def drafts(self) -> list[RecommendationDraft]:
        return [*self.recommendation_drafts, *self.early_signal_drafts, *self.review_drafts]


@dataclass(frozen=True)
class IngestSummary:
    items: int
    stories: int
    credits: float
    degraded: bool
    skipped: Mapping[str, int]


def _embed(embedder: Embedder | None, texts: Sequence[str], role: str) -> NDArray[np.float32] | None:
    if embedder is None or not texts:
        return None
    try:
        return embedder.embed(texts, role=role)
    except EmbedderUnavailable:
        return None


class NewsRadarService:
    """facade for the global poll cron and the per-client news stage."""

    def __init__(
        self,
        *,
        settings: Settings,
        uow: Callable[[], UnitOfWork],
        redis: Redis,
        packs: SourcePackRegistry,
        poller: NewsPoller,
        scanner_for: Callable[[NewsClientContext, UnitOfWork], NewsScanner],
        embedder: Embedder | None = None,
    ) -> None:
        self._settings = settings
        self._news = settings.news
        self._uow = uow
        self._redis = redis
        self._packs = packs
        self._poller = poller
        self._scanner_for = scanner_for
        self._embedder = embedder
        self._planner = QueryPlanner(settings.news)

    def _clusterer(self) -> StoryClusterer:
        return StoryClusterer(
            fuzzy_min_ratio=self._news.fuzzy_title_min_ratio,
            fuzzy_window=timedelta(hours=self._news.fuzzy_title_window_hours),
            embedding_min_cosine=self._news.embedding_merge_min_cosine,
            developing_window=timedelta(hours=self._news.developing_window_hours),
        )

    async def _ingest(self, uow: UnitOfWork, items: Sequence[NewsItem], now: datetime) -> int:
        store = NewsItemStore(uow.session)
        vectors = _embed(self._embedder, [f"{i.title} {i.snippet[:300]}" for i in items], "passage")
        by_item = {item.id: vectors[index] for index, item in enumerate(items)} if vectors is not None else {}
        await store.upsert_items(items, by_item)
        recent = await store.recent(now - CLUSTER_WINDOW)
        stored_vectors = await store.vectors([item.id for item in recent]) if self._embedder else {}
        clustered = self._clusterer().cluster(recent, now, stored_vectors)
        await store.save_stories(clustered.stories, clustered.assignments, clustered.centroids)
        return len(clustered.stories)

    async def _run_poll(
        self, uow: UnitOfWork, now: datetime, client_id: UUID | None, force: bool
    ) -> IngestSummary:
        queries = NewsQueryStore(uow.session)
        due = await queries.due(now, POLL_BATCH, client_id=client_id, force=force)
        result = await self._poller.poll(due, now)
        for run in result.runs:
            await queries.record_run(
                run.query_id,
                started_at=run.started_at,
                finished_at=run.finished_at,
                status=run.status,
                cost=run.cost,
                item_count=run.item_count,
                error=run.error,
                request_id=run.request_id,
                next_run_at=run.next_run_at,
            )
        unique = list({item.url_hash: item for item in result.items}.values())
        stories = await self._ingest(uow, unique, now) if unique else 0
        return IngestSummary(len(unique), stories, result.credits, result.degraded, dict(result.skipped))

    async def poll_due(self, now: datetime) -> IngestSummary:
        """global cron entry point (every 15 min); clients never trigger fetches directly."""
        async with self._uow() as uow:
            summary = await self._run_poll(uow, now, None, False)
            await uow.commit()
        return summary

    async def _force_allowed(self, client_id: UUID) -> bool:
        key = f"{self._settings.redis.key_prefix}:news:force:{client_id}"
        return bool(await self._redis.set(key, "1", nx=True, ex=self._news.on_demand_cooldown_minutes * 60))

    def _business_vector(self, ctx: NewsClientContext) -> NDArray[np.float32] | None:
        texts = [o.name for o in ctx.profile.offerings]
        texts += [v.statement for v in ctx.profile.value_propositions]
        texts += [q.text for q in ctx.active_buyer_questions]
        vectors = _embed(self._embedder, texts, "query")
        if vectors is None:
            return None
        mean = vectors.mean(axis=0)
        norm = float(np.linalg.norm(mean))
        return (mean / norm).astype(np.float32) if norm > 0 else None

    def _message(self, ctx: NewsClientContext, outcome: ScanOutcome, capped: int) -> str | None:
        if outcome.drafts:
            if capped:
                total = len(outcome.drafts) + capped
                return f"Showing the {len(outcome.drafts)} most relevant of {total} news items. See all in News radar."
            return None
        if ctx.level.value in QUIET_LEVELS or not outcome.signals:
            watched = ", ".join(e.name for e in ctx.freshness.watch_entities[:3]) or "your niche"
            return f"No news worth a video this week — normal for your niche. We'll keep watching {watched}."
        return None

    def _report(
        self,
        ctx: NewsClientContext,
        outcome: ScanOutcome,
        now: datetime,
        planned: int,
        dropped: int,
        ingest: IngestSummary | None,
    ) -> NewsRadarReport:
        by_surface: dict[str, list[RecommendationDraft]] = {
            SURFACE_RECOMMENDATIONS: [],
            SURFACE_EARLY_SIGNALS: [],
            SURFACE_REVIEW: [],
        }
        for draft in outcome.drafts:
            by_surface[surface_of(draft)].append(draft)
        gate_counts: dict[str, int] = {}
        for _draft, result in outcome.gate_suppressed:
            gate_counts[result.reason_code or "unknown"] = (
                gate_counts.get(result.reason_code or "unknown", 0) + 1
            )
        suppressed = dict(outcome.suppressed_stories)
        for signal in outcome.signals:
            if signal.suppression_reason:
                suppressed[signal.suppression_reason] = suppressed.get(signal.suppression_reason, 0) + 1
        capped = suppressed.get("cap", 0)
        return NewsRadarReport(
            client_id=ctx.client_id,
            as_of=now,
            profile_version=ctx.profile_version,
            level=ctx.level.value,
            recommendation_drafts=by_surface[SURFACE_RECOMMENDATIONS],
            early_signal_drafts=by_surface[SURFACE_EARLY_SIGNALS],
            review_drafts=by_surface[SURFACE_REVIEW],
            signal_ids=[s.id for s in outcome.signals],
            refresh_signal_ids=[s.id for s in outcome.signals if s.refresh_eligible],
            withdrawals=[NewsWithdrawal(**asdict(w)) for w in outcome.withdrawals],
            suppressed=suppressed,
            gate_suppressed=gate_counts,
            queries_planned=planned,
            queries_dropped=dropped,
            tavily_credits=ingest.credits if ingest else 0.0,
            llm_calls=len(outcome.new_judgements),
            degraded=bool(ingest and ingest.degraded),
            message=self._message(ctx, outcome, capped),
        )

    async def scan(
        self, ctx: NewsClientContext, *, now: datetime, run_id: UUID | None = None, force: bool = False
    ) -> NewsRadarReport:
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        packs = self._packs.resolve(ctx.freshness.source_packs)
        plan = self._planner.plan(ctx, packs, now)
        async with self._uow() as uow:
            await NewsQueryStore(uow.session).sync_subscriptions(
                scope, plan.subscriptions, ctx.profile_version, now
            )
            await uow.commit()
        ingest = None
        if force and await self._force_allowed(ctx.client_id):
            async with self._uow() as uow:
                ingest = await self._run_poll(uow, now, ctx.client_id, True)
                await uow.commit()
        async with self._uow() as uow:
            items = NewsItemStore(uow.session)
            signals = NewsSignalStore(uow.session, scope, self._settings.policy.retention_days)
            rows = await items.stories_since(now - CLUSTER_WINDOW)
            bundles = [StoryBundle(story, members) for story, members in rows]
            story_ids = [bundle.story.id for bundle in bundles]
            inputs = ScanInputs(
                ctx=ctx,
                bundles=bundles,
                now=now,
                run_id=run_id,
                recent_items=[item for bundle in bundles for item in bundle.items],
                existing_signals=await signals.live_signals(now - LIFECYCLE_WINDOW),
                judgements=await signals.judgements(story_ids),
                past_entities=await signals.past_entities(),
                centroids=await items.story_centroids(story_ids) if self._embedder else {},
                business_vector=self._business_vector(ctx),
            )
            outcome = await self._scanner_for(ctx, uow).scan(inputs)
            await signals.save_judgements(outcome.new_judgements)
            await signals.save_signals(outcome.signals, now)
            for signal_id, reason in outcome.review:
                await signals.enqueue_review(signal_id, reason)
            await uow.commit()
        return self._report(ctx, outcome, now, len(plan.subscriptions), len(plan.dropped), ingest)
