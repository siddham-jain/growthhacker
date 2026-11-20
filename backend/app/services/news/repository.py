import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.domain.news import (
    CredibilityAssessment,
    DemandCheck,
    EarlyVideo,
    NewsItem,
    NewsJudgement,
    NewsSignal,
    NewsSourceRef,
    NewsStory,
    SafetyDecision,
    SourceTier,
)
from app.models.news import NewsItem as NewsItemRow
from app.models.news import NewsJudgement as NewsJudgementRow
from app.models.news import NewsQuery as NewsQueryRow
from app.models.news import NewsQueryRun as NewsQueryRunRow
from app.models.news import NewsQuerySubscription as SubscriptionRow
from app.models.news import NewsReviewQueueItem as ReviewRow
from app.models.news import NewsSignal as NewsSignalRow
from app.models.news import NewsStory as NewsStoryRow
from app.models.news import SourceTier as SourceTierRow
from app.models.news import YtDemandCheck as DemandRow
from app.models.youtube_cache import YtVideo
from app.ports.freshness import PlannedQuery
from app.services.news.demand import CandidateVideo, DemandOutcome
from app.services.news.history import PastNewsEntity
from app.services.news.query_planner import PlannedSubscription
from app.services.news.scanner import StoredJudgement

JUDGEMENT_NAMESPACE = uuid.UUID("2b7c9e14-5d3a-4f08-8c61-7a9e0b1d2c35")
WITHDRAWN = ("retracted", "superseded")
SHOWABLE_STATUSES = ("new", "drafted", "shown", "used")


def judgement_id_for(client_id: uuid.UUID, story_id: uuid.UUID, profile_version: int) -> uuid.UUID:
    return uuid.uuid5(JUDGEMENT_NAMESPACE, f"{client_id}:{story_id}:{profile_version}")


def vector_bytes(vector: NDArray[np.float32] | None) -> bytes | None:
    return None if vector is None else np.asarray(vector, dtype=np.float32).tobytes()


def vector_from(raw: bytes | None) -> NDArray[np.float32] | None:
    return None if raw is None else np.frombuffer(raw, dtype=np.float32)


@dataclass(frozen=True)
class DueQuery:
    id: uuid.UUID
    planned: PlannedQuery
    last_run_at: datetime | None
    weight: float


class NewsQueryStore:
    """global shared queries plus tenant subscriptions; one fetch serves every subscriber."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, planned: PlannedQuery, now: datetime) -> uuid.UUID:
        statement = insert(NewsQueryRow).values(
            source_key=planned.source_key,
            normalized_query=planned.normalized_query,
            params=planned.params,
            query_hash=planned.query_hash,
            cadence_minutes=planned.cadence_minutes,
            next_run_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[NewsQueryRow.query_hash],
            set_={
                "cadence_minutes": func.least(
                    NewsQueryRow.cadence_minutes, statement.excluded.cadence_minutes
                ),
                "orphaned_at": None,
            },
        ).returning(NewsQueryRow.id)
        return (await self.session.execute(statement)).scalar_one()

    async def _refresh_counts(self, query_ids: set[uuid.UUID], now: datetime) -> None:
        for query_id in query_ids:
            count = (
                await self.session.execute(
                    select(func.count())
                    .select_from(SubscriptionRow)
                    .where(SubscriptionRow.query_id == query_id)
                )
            ).scalar_one()
            await self.session.execute(
                update(NewsQueryRow)
                .where(NewsQueryRow.id == query_id)
                .values(
                    subscriber_count=count,
                    orphaned_at=func.coalesce(NewsQueryRow.orphaned_at, now) if count == 0 else None,
                )
            )

    async def sync_subscriptions(
        self, scope: TenantScope, plan: Sequence[PlannedSubscription], profile_version: int, now: datetime
    ) -> list[uuid.UUID]:
        if scope.client_id is None:
            raise ValueError("subscriptions need a client scope")
        wanted: dict[uuid.UUID, PlannedSubscription] = {}
        for subscription in plan:
            wanted[await self.upsert(subscription.query, now)] = subscription
        owned = (
            SubscriptionRow.workspace_id == scope.workspace_id,
            SubscriptionRow.client_id == scope.client_id,
        )
        previous = set((await self.session.execute(select(SubscriptionRow.query_id).where(*owned))).scalars())
        stale = previous - set(wanted)
        if stale:
            await self.session.execute(
                delete(SubscriptionRow).where(*owned, SubscriptionRow.query_id.in_(stale))
            )
        for query_id, subscription in wanted.items():
            statement = insert(SubscriptionRow).values(
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                query_id=query_id,
                origin=subscription.origin,
                weight=subscription.weight,
                profile_version=profile_version,
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SubscriptionRow.client_id, SubscriptionRow.query_id],
                    set_={
                        "origin": subscription.origin,
                        "weight": subscription.weight,
                        "profile_version": profile_version,
                    },
                )
            )
        await self._refresh_counts(stale | set(wanted), now)
        return list(wanted)

    async def due(
        self, now: datetime, limit: int, *, client_id: uuid.UUID | None = None, force: bool = False
    ) -> list[DueQuery]:
        weight = func.max(SubscriptionRow.weight).label("weight")
        statement = (
            select(NewsQueryRow, weight)
            .join(SubscriptionRow, SubscriptionRow.query_id == NewsQueryRow.id)
            .where(NewsQueryRow.subscriber_count > 0)
            .group_by(NewsQueryRow.id)
            .order_by(weight.desc(), NewsQueryRow.next_run_at)
            .limit(limit)
        )
        if not force:
            statement = statement.where(NewsQueryRow.next_run_at <= now)
        if client_id is not None:
            statement = statement.where(SubscriptionRow.client_id == client_id)
        rows = (await self.session.execute(statement)).all()
        return [
            DueQuery(
                id=row.id,
                planned=PlannedQuery(
                    source_key=row.source_key,
                    normalized_query=row.normalized_query,
                    params=row.params,
                    query_hash=row.query_hash,
                    cadence_minutes=row.cadence_minutes,
                ),
                last_run_at=row.last_run_at,
                weight=float(row_weight),
            )
            for row, row_weight in rows
        ]

    async def record_run(
        self,
        query_id: uuid.UUID,
        *,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        cost: float,
        item_count: int,
        error: str | None,
        request_id: str | None,
        next_run_at: datetime,
    ) -> None:
        self.session.add(
            NewsQueryRunRow(
                query_id=query_id,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                cost_credits=Decimal(str(cost)),
                item_count=item_count,
                error=error,
                provider_request_id=request_id,
            )
        )
        values: dict[str, Any] = {"last_status": status, "next_run_at": next_run_at}
        if status in {"ok", "partial", "degraded"}:
            values["last_run_at"] = finished_at
        await self.session.execute(update(NewsQueryRow).where(NewsQueryRow.id == query_id).values(**values))
        await self.session.flush()


def _item_from(row: NewsItemRow) -> NewsItem:
    return NewsItem(
        id=row.id,
        source_key=row.source_key,
        url=row.url,
        canonical_url=row.canonical_url,
        url_hash=row.url_hash,
        domain=row.domain,
        title=row.title,
        snippet=row.snippet,
        language=row.language,
        published_at=row.published_at,
        published_precision=row.published_precision,
        fetched_at=row.fetched_at,
        source_rank_score=row.source_rank_score,
        external_ids=dict(row.external_ids),
        source_tier=SourceTier(row.source_tier),
        flags=list(row.flags),
        story_id=row.story_id,
    )


class NewsItemStore:
    """global items and stories; snippets only, no client data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_items(
        self, items: Sequence[NewsItem], vectors: Mapping[uuid.UUID, NDArray[np.float32]] | None = None
    ) -> list[uuid.UUID]:
        vectors = vectors or {}
        ids = []
        for item in items:
            values = item.model_dump(exclude={"story_id"}) | {
                "flags": list(item.flags),
                "source_tier": item.source_tier.value,
                "embedding": vector_bytes(vectors.get(item.id)),
            }
            statement = insert(NewsItemRow).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[NewsItemRow.url_hash],
                set_={
                    "published_at": func.least(NewsItemRow.published_at, statement.excluded.published_at),
                    "source_rank_score": statement.excluded.source_rank_score,
                },
            ).returning(NewsItemRow.id)
            ids.append((await self.session.execute(statement)).scalar_one())
        return ids

    async def recent(self, since: datetime) -> list[NewsItem]:
        rows = await self.session.execute(
            select(NewsItemRow).where(
                or_(
                    NewsItemRow.published_at >= since,
                    and_(NewsItemRow.published_at.is_(None), NewsItemRow.fetched_at >= since),
                )
            )
        )
        return [_item_from(row) for row in rows.scalars()]

    async def vectors(self, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, NDArray[np.float32]]:
        rows = await self.session.execute(
            select(NewsItemRow.id, NewsItemRow.embedding).where(
                NewsItemRow.id.in_(item_ids), NewsItemRow.embedding.is_not(None)
            )
        )
        return {row.id: vector for row in rows if (vector := vector_from(row.embedding)) is not None}

    async def save_stories(
        self,
        stories: Sequence[NewsStory],
        assignments: Mapping[uuid.UUID, uuid.UUID],
        centroids: Mapping[uuid.UUID, NDArray[np.float32]],
    ) -> None:
        for story in stories:
            values = {
                "id": story.id,
                "first_published_at": story.first_published_at,
                "last_published_at": story.last_published_at,
                "outlet_count": story.outlet_count,
                "best_tier": story.best_tier.value,
                "version_tokens": list(story.version_tokens),
                "centroid": vector_bytes(centroids.get(story.id)),
                "status": story.status,
            }
            statement = insert(NewsStoryRow).values(**values)
            await self.session.execute(
                statement.on_conflict_do_update(index_elements=[NewsStoryRow.id], set_=values)
            )
        for item_id, story_id in assignments.items():
            await self.session.execute(
                update(NewsItemRow).where(NewsItemRow.id == item_id).values(story_id=story_id)
            )
        for story in stories:
            await self.session.execute(
                update(NewsStoryRow)
                .where(NewsStoryRow.id == story.id)
                .values(representative_item_id=story.representative_item_id)
            )
        await self.session.flush()

    async def stories_since(self, since: datetime) -> list[tuple[NewsStory, list[NewsItem]]]:
        story_rows = (
            (await self.session.execute(select(NewsStoryRow).where(NewsStoryRow.last_published_at >= since)))
            .scalars()
            .all()
        )
        if not story_rows:
            return []
        item_rows = (
            (
                await self.session.execute(
                    select(NewsItemRow).where(NewsItemRow.story_id.in_([s.id for s in story_rows]))
                )
            )
            .scalars()
            .all()
        )
        members: dict[uuid.UUID, list[NewsItem]] = {}
        for row in item_rows:
            members.setdefault(row.story_id, []).append(_item_from(row))
        bundles = []
        for row in story_rows:
            items = members.get(row.id, [])
            if not items:
                continue
            story = NewsStory(
                id=row.id,
                first_published_at=row.first_published_at,
                last_published_at=row.last_published_at,
                item_ids=[item.id for item in items],
                outlet_count=row.outlet_count,
                best_tier=SourceTier(row.best_tier),
                representative_item_id=row.representative_item_id or items[0].id,
                version_tokens=list(row.version_tokens),
                status=row.status,
            )
            bundles.append((story, items))
        return bundles

    async def story_centroids(self, story_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, NDArray[np.float32]]:
        rows = await self.session.execute(
            select(NewsStoryRow.id, NewsStoryRow.centroid).where(
                NewsStoryRow.id.in_(story_ids), NewsStoryRow.centroid.is_not(None)
            )
        )
        return {row.id: vector for row in rows if (vector := vector_from(row.centroid)) is not None}

    async def curated_tiers(self) -> dict[str, SourceTier]:
        rows = await self.session.execute(select(SourceTierRow.domain, SourceTierRow.tier))
        return {row.domain: SourceTier(row.tier) for row in rows}


def _demand_from(raw: Mapping[str, Any] | None, entity: str, fallback_at: datetime) -> DemandCheck:
    if raw:
        return DemandCheck.model_validate(raw)
    # demand-derived fields are nulled at day 28; the signal keeps only its news facts
    return DemandCheck(
        status="unchecked_error",
        entity_query=entity,
        published_after=fallback_at,
        checked_at=fallback_at,
        early_video_count=0,
        channel_count=0,
        outlier_count=0,
        median_views_per_hour=None,
        saturation="empty",
        quota_units_spent=0,
    )


class NewsSignalStore:
    """tenant-scoped judgements, signals and the review queue; every query filters by scope."""

    def __init__(self, session: AsyncSession, scope: TenantScope, retention_days: int = 28) -> None:
        if scope.client_id is None:
            raise ValueError("news signals need a client scope")
        self.session = session
        self.scope = scope
        self._retention = timedelta(days=retention_days)

    def _owned(self, model: Any) -> tuple[Any, Any]:
        return model.workspace_id == self.scope.workspace_id, model.client_id == self.scope.client_id

    async def judgements(self, story_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, StoredJudgement]:
        if not story_ids:
            return {}
        rows = await self.session.execute(
            select(NewsJudgementRow)
            .where(*self._owned(NewsJudgementRow), NewsJudgementRow.story_id.in_(story_ids))
            .order_by(NewsJudgementRow.profile_version)
        )
        stored: dict[uuid.UUID, StoredJudgement] = {}
        for row in rows.scalars():
            payload = row.judgement
            stored[row.story_id] = StoredJudgement(
                story_id=row.story_id,
                profile_version=row.profile_version,
                judgement=NewsJudgement.model_validate(payload["judgement"]),
                aliases=list(payload.get("aliases", [])),
                refresh_keywords=list(payload.get("refresh_keywords", [])),
                outlet_count=int(payload.get("outlet_count", 0)),
                best_tier=SourceTier(payload.get("best_tier", SourceTier.tier3_low.value)),
                model=row.model,
                tokens_in=row.tokens_in,
                tokens_out=row.tokens_out,
            )
        return stored

    async def save_judgements(self, judgements: Sequence[StoredJudgement]) -> None:
        for stored in judgements:
            payload = {
                "judgement": stored.judgement.model_dump(mode="json"),
                "aliases": stored.aliases,
                "refresh_keywords": stored.refresh_keywords,
                "outlet_count": stored.outlet_count,
                "best_tier": stored.best_tier.value,
            }
            values = {
                "id": judgement_id_for(self.scope.client_id, stored.story_id, stored.profile_version),
                "workspace_id": self.scope.workspace_id,
                "client_id": self.scope.client_id,
                "story_id": stored.story_id,
                "profile_version": stored.profile_version,
                "model": stored.model,
                "judgement": payload,
                "grounded": True,
                "tokens_in": stored.tokens_in,
                "tokens_out": stored.tokens_out,
            }
            statement = insert(NewsJudgementRow).values(**values)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        NewsJudgementRow.client_id,
                        NewsJudgementRow.story_id,
                        NewsJudgementRow.profile_version,
                    ],
                    set_={k: values[k] for k in ("model", "judgement", "tokens_in", "tokens_out")},
                )
            )
        await self.session.flush()

    async def save_signals(self, signals: Sequence[NewsSignal], now: datetime) -> None:
        ordered = sorted(signals, key=lambda s: s.superseded_by is not None)
        for signal in ordered:
            demand = signal.demand
            has_demand = demand.status in {"checked", "local_only"}
            values = {
                "id": signal.id,
                "workspace_id": self.scope.workspace_id,
                "client_id": self.scope.client_id,
                "story_id": signal.story_id,
                "judgement_id": judgement_id_for(
                    self.scope.client_id, signal.story_id, signal.profile_version
                ),
                "profile_version": signal.profile_version,
                "entity": signal.entity,
                "aliases": list(signal.aliases),
                "entity_kind": signal.entity_kind,
                "is_competitor_entity": signal.is_competitor_entity,
                "topic_ref_ids": list(signal.topic_ref_ids),
                "first_seen_at": signal.first_seen_at,
                "event_date": signal.event_date,
                "sources": [s.model_dump(mode="json") for s in signal.sources],
                "credibility": signal.credibility.model_dump(mode="json"),
                "demand": demand.model_dump(mode="json"),
                "demand_video_ids": signal.demand_video_ids or None,
                "demand_expires_at": demand.checked_at + self._retention if has_demand else None,
                "verification_status": signal.verification_status,
                "superseded_by": signal.superseded_by,
                "relevance_score": signal.relevance_score,
                "urgency": signal.urgency,
                "half_life_hours": signal.half_life_hours,
                "expires_at": signal.expires_at,
                "refresh_keywords": list(signal.refresh_keywords),
                "refresh_eligible": signal.refresh_eligible,
                "safety": signal.safety.model_dump(mode="json"),
                "status": signal.status,
                "suppression_reason": signal.suppression_reason,
                "created_at": signal.created_at,
                "updated_at": now,
            }
            statement = insert(NewsSignalRow).values(**values)
            mutable = {
                k: v for k, v in values.items() if k not in {"id", "workspace_id", "client_id", "created_at"}
            }
            await self.session.execute(
                statement.on_conflict_do_update(index_elements=[NewsSignalRow.id], set_=mutable)
            )
        await self.session.flush()

    async def _signals(self, *conditions: Any) -> list[NewsSignal]:
        rows = await self.session.execute(
            select(NewsSignalRow, NewsJudgementRow.judgement)
            .join(NewsJudgementRow, NewsJudgementRow.id == NewsSignalRow.judgement_id)
            .where(*self._owned(NewsSignalRow), *conditions)
            .order_by(NewsSignalRow.first_seen_at.desc())
        )
        signals = []
        for row, judgement in rows.all():
            signals.append(
                NewsSignal(
                    id=row.id,
                    client_id=row.client_id,
                    story_id=row.story_id,
                    profile_version=row.profile_version,
                    judgement=NewsJudgement.model_validate(judgement["judgement"]),
                    credibility=CredibilityAssessment.model_validate(row.credibility),
                    demand=_demand_from(row.demand, row.entity, row.first_seen_at),
                    aliases=list(row.aliases),
                    is_competitor_entity=row.is_competitor_entity,
                    topic_ref_ids=list(row.topic_ref_ids),
                    first_seen_at=row.first_seen_at,
                    sources=[NewsSourceRef.model_validate(s) for s in row.sources],
                    verification_status=row.verification_status,
                    superseded_by=row.superseded_by,
                    relevance_score=row.relevance_score,
                    urgency=row.urgency,
                    half_life_hours=row.half_life_hours,
                    expires_at=row.expires_at,
                    refresh_keywords=list(row.refresh_keywords),
                    refresh_eligible=row.refresh_eligible,
                    safety=SafetyDecision.model_validate(row.safety),
                    status=row.status,
                    suppression_reason=row.suppression_reason,
                    created_at=row.created_at,
                )
            )
        return signals

    async def live_signals(self, since: datetime) -> list[NewsSignal]:
        # signals the lifecycle still has to watch: not yet withdrawn and seen inside the window
        return await self._signals(
            NewsSignalRow.verification_status.not_in(WITHDRAWN), NewsSignalRow.first_seen_at >= since
        )

    async def active_for_client(self, now: datetime) -> list[NewsSignal]:
        """09's read model: refresh-eligible, verified, unexpired signals."""
        return await self._signals(
            NewsSignalRow.refresh_eligible.is_(True),
            NewsSignalRow.verification_status == "active",
            NewsSignalRow.expires_at > now,
            NewsSignalRow.status.in_(SHOWABLE_STATUSES),
        )

    async def withdrawn_since(self, since: datetime) -> list[NewsSignal]:
        """signals retracted, superseded, disputed or expired since a time; 07 pulls cards, 09 rolls back."""
        return await self._signals(
            NewsSignalRow.updated_at >= since,
            or_(NewsSignalRow.verification_status != "active", NewsSignalRow.status == "expired"),
        )

    async def past_entities(self) -> list[PastNewsEntity]:
        rows = await self.session.execute(
            select(NewsSignalRow.entity, NewsSignalRow.first_seen_at).where(*self._owned(NewsSignalRow))
        )
        return [PastNewsEntity(entity=row.entity, first_seen_at=row.first_seen_at) for row in rows]

    async def enqueue_review(self, signal_id: uuid.UUID, reason: str) -> bool:
        pending = await self.session.execute(
            select(ReviewRow.id).where(
                *self._owned(ReviewRow), ReviewRow.signal_id == signal_id, ReviewRow.decision.is_(None)
            )
        )
        if pending.first() is not None:
            return False
        self.session.add(
            ReviewRow(
                workspace_id=self.scope.workspace_id,
                client_id=self.scope.client_id,
                signal_id=signal_id,
                reason=reason,
            )
        )
        await self.session.flush()
        return True


class DemandCheckStore:
    """shared yt_demand_checks rows (28-day hard delete); one check per entity per day serves every client."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_norm: str, not_before: datetime) -> DemandOutcome | None:
        row = (
            await self.session.execute(
                select(DemandRow)
                .where(DemandRow.entity_norm == entity_norm, DemandRow.checked_at >= not_before)
                .order_by(DemandRow.checked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        videos = {}
        early = []
        for entry in row.early_videos:
            video = EarlyVideo.model_validate(entry["video"])
            early.append(video)
            videos[video.video_id] = CandidateVideo(
                video_id=video.video_id,
                channel_id=video.channel_id,
                channel_title=video.channel_title,
                title=entry["title"],
                description="",
                published_at=video.published_at,
                views=video.views,
                thumbnail_url=entry.get("thumbnail_url"),
            )
        check = DemandCheck.model_validate(
            {**row.aggregates, "early_videos": [v.model_dump(mode="json") for v in early]}
        )
        return DemandOutcome(check, videos)

    async def put(self, entity_norm: str, day: date, query: str, outcome: DemandOutcome) -> None:
        check = outcome.check
        early = [
            {
                "video": video.model_dump(mode="json"),
                "title": outcome.videos[video.video_id].title if video.video_id in outcome.videos else "",
                "thumbnail_url": outcome.videos[video.video_id].thumbnail_url
                if video.video_id in outcome.videos
                else None,
            }
            for video in check.early_videos
        ]
        values = {
            "entity_norm": entity_norm,
            "day_bucket": day,
            "query": query,
            "published_after": check.published_after,
            "video_ids": [v.video_id for v in check.early_videos],
            "early_videos": early,
            "aggregates": check.model_dump(mode="json", exclude={"early_videos"}),
            "quota_units": check.quota_units_spent,
            "checked_at": check.checked_at,
            "status": check.status,
        }
        statement = insert(DemandRow).values(**values)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[DemandRow.entity_norm, DemandRow.day_bucket],
                set_={k: v for k, v in values.items() if k not in {"entity_norm", "day_bucket"}},
            )
        )
        await self.session.flush()


class CachedVideoSearch:
    """free first pass over the shared video cache (02) before any search call."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, terms: Sequence[str], published_after: datetime, limit: int) -> list[CandidateVideo]:
        patterns = [f"%{term.replace('%', '').replace('_', ' ')}%" for term in terms if term.strip()]
        if not patterns:
            return []
        matches = or_(
            *(YtVideo.title.ilike(p) for p in patterns), *(YtVideo.description.ilike(p) for p in patterns)
        )
        rows = await self.session.execute(
            select(YtVideo)
            .where(matches, YtVideo.published_at >= published_after, YtVideo.availability == "available")
            .order_by(YtVideo.view_count.desc().nulls_last())
            .limit(limit)
        )
        return [
            CandidateVideo(
                video_id=row.video_id,
                channel_id=row.channel_id,
                channel_title=row.channel_title or "",
                title=row.title or "",
                description=row.description or "",
                published_at=row.published_at,
                views=row.view_count or 0,
                duration_s=row.duration_s,
                thumbnail_url=row.thumbnail_url,
            )
            for row in rows.scalars()
        ]
