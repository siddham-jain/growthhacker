from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.recommendations import Recommendation
from app.domain.topics import TopicKind
from app.evaluation.review.models import (
    ClientCards,
    ClientSheet,
    ReviewCard,
    ReviewReference,
    SourceCard,
    UniverseVideo,
)
from app.models.recommendations import Recommendation as RecommendationRow
from app.models.runs import ActiveResultSet, PipelineRun, RunArtifact
from app.models.tenancy import Client
from app.models.website import BusinessProfile
from app.models.youtube_cache import YtChannel, YtVideo
from app.pipeline.contracts import RunConfigSnapshot
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT

UNIVERSE_VIDEOS = 20


class CardSource(Protocol):
    async def load(self) -> list[ClientCards]: ...


def _views_label(reference: Any) -> str:
    age = reference.views_at_age
    if age.views is None:
        return age.label
    return f"{age.views:,} views at day {age.age_days} ({age.label})"


def card_from_recommendation(rec: Recommendation, client_name: str) -> ReviewCard:
    confidence = rec.confidence
    return ReviewCard(
        client_name=client_name,
        type=rec.type.value,
        summary=rec.summary,
        lens=tuple(lens.value for lens in rec.lens),
        buyer_question=rec.buyer_question,
        why_it_works=tuple(rec.why_it_works),
        reference_videos=tuple(
            ReviewReference(
                title=ref.title,
                channel=ref.channel,
                url=ref.url,
                published_on=ref.published_at.date(),
                views_label=_views_label(ref),
                outlier_ratio=ref.outlier_ratio,
                thumbnail_url=ref.thumbnail_url,
            )
            for ref in rec.reference_videos
        ),
        fit_to_client=rec.fit_to_client,
        expected_range=rec.expected_range_vs_baseline,
        confidence=f"{confidence.label.value} · {confidence.outliers} outliers across "
        f"{confidence.channels} channels",
    )


def gate_features(rec: Recommendation, draft: Mapping[str, Any] | None) -> dict[str, float]:
    """numbers the gates saw: confidence counts, reference ratios, expected range, per-gate features."""
    ratios = [ref.outlier_ratio for ref in rec.reference_videos if ref.outlier_ratio is not None]
    features: dict[str, float] = {
        "confidence.outliers": float(rec.confidence.outliers),
        "confidence.channels": float(rec.confidence.channels),
        "references": float(len(rec.reference_videos)),
    }
    if ratios:
        features["min_reference_ratio"] = float(min(ratios))
    expected = rec.extras.expected_range
    if expected is not None and expected.low is not None and expected.high is not None:
        features["expected_range.low"] = float(expected.low)
        features["expected_range.high"] = float(expected.high)
    for result in (draft or {}).get("gate_results", []):
        for name, value in (result.get("features") or {}).items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                features[f"{result.get('gate')}.{name}"] = float(value)
    score = (draft or {}).get("score") or {}
    if isinstance(score.get("total"), int | float):
        features["score.total"] = float(score["total"])
    return dict(sorted(features.items()))


def source_card(rec: Recommendation, client_name: str, row: RecommendationRow | None) -> SourceCard:
    return SourceCard(
        rec_id=rec.id,
        db_id=row.id if row is not None else None,
        type=rec.type.value,
        lens=tuple(lens.value for lens in rec.lens),
        confidence_label=rec.confidence.label.value,
        card=card_from_recommendation(rec, client_name),
        gate_features=gate_features(rec, row.draft if row is not None else None),
    )


def _profile_summary(profile: Mapping[str, Any] | None) -> tuple[str | None, tuple[str, ...]]:
    if not profile:
        return None, ()
    identity = profile.get("identity") or {}
    summary = identity.get("description") or identity.get("one_liner")
    offerings = tuple(str(o.get("name")) for o in profile.get("offerings") or [] if o.get("name"))
    return summary, offerings[:12]


class PublishedCardSource:
    """the cards each client sees now: the latest published result set and its recommend artifact."""

    def __init__(self, session_factory: Any, client_ids: Sequence[UUID] | None = None) -> None:
        self.session_factory = session_factory
        self.client_ids = list(client_ids or [])

    async def load(self) -> list[ClientCards]:
        async with self.session_factory() as session:
            statement = select(Client).where(Client.archived_at.is_(None)).order_by(Client.id)
            if self.client_ids:
                statement = statement.where(Client.id.in_(self.client_ids))
            clients = (await session.execute(statement)).scalars().all()
            loaded = [await self._client(session, client) for client in clients]
        return [item for item in loaded if item is not None]

    async def _client(self, session: AsyncSession, client: Client) -> ClientCards | None:
        active = (
            await session.execute(
                select(ActiveResultSet)
                .where(ActiveResultSet.client_id == client.id)
                .order_by(ActiveResultSet.published_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if active is None:
            return None
        artifact = (
            await session.execute(
                select(RunArtifact).where(
                    RunArtifact.workspace_id == client.workspace_id,
                    RunArtifact.client_id == client.id,
                    RunArtifact.run_id == active.run_id,
                    RunArtifact.key == RECOMMEND_ARTIFACT.name,
                    RunArtifact.schema_version == RECOMMEND_ARTIFACT.schema_version,
                )
            )
        ).scalar_one_or_none()
        run = await session.get(PipelineRun, active.run_id)
        if artifact is None or run is None:
            return None
        output = RECOMMEND_ARTIFACT.model.model_validate(artifact.payload)
        window = output.window(active.window_days)
        if window is None:
            return None
        snapshot = RunConfigSnapshot.model_validate(run.config_snapshot)
        recs = [*window.set.recommendations, *window.refresh_cards]
        rows = {
            row.stable_key: row
            for row in (
                await session.execute(select(RecommendationRow).where(RecommendationRow.run_id == run.id))
            )
            .scalars()
            .all()
        }
        cards = tuple(source_card(rec, client.name, rows.get(rec.extras.stable_key)) for rec in recs)
        universe, fetched = await self._universe(session, snapshot, output.as_of, active.window_days)
        observed = [artifact.api_data_observed_at or artifact.created_at, *fetched]
        return ClientCards(
            client_id=client.id,
            workspace_id=client.workspace_id,
            run_id=run.id,
            as_of=output.as_of,
            window_days=active.window_days,
            sheet=await self._sheet(session, client, snapshot, active.window_days),
            cards=cards,
            universe=tuple(universe),
            oldest_api_at=min(observed),
        )

    async def _universe(
        self, session: AsyncSession, snapshot: RunConfigSnapshot, as_of: datetime, window_days: int
    ) -> tuple[list[UniverseVideo], list[datetime]]:
        if not snapshot.competitor_channel_ids:
            return [], []
        rows = (
            (
                await session.execute(
                    select(YtVideo)
                    .where(
                        YtVideo.channel_id.in_(snapshot.competitor_channel_ids),
                        YtVideo.published_at >= as_of - timedelta(days=window_days),
                        YtVideo.published_at <= as_of,
                        YtVideo.availability == "available",
                        YtVideo.view_count.is_not(None),
                    )
                    .order_by(YtVideo.view_count.desc(), YtVideo.published_at, YtVideo.video_id)
                    .limit(UNIVERSE_VIDEOS)
                )
            )
            .scalars()
            .all()
        )
        videos = [
            UniverseVideo(
                video_id=row.video_id,
                channel_id=row.channel_id,
                channel_title=row.channel_title or row.channel_id,
                title=row.title or row.video_id,
                published_at=row.published_at,
                views=row.view_count or 0,
                thumbnail_url=row.thumbnail_url,
            )
            for row in rows
        ]
        return videos, [min(row.metadata_fetched_at, row.stats_fetched_at) for row in rows]

    async def _sheet(
        self, session: AsyncSession, client: Client, snapshot: RunConfigSnapshot, window_days: int
    ) -> ClientSheet:
        profile = (
            await session.execute(
                select(BusinessProfile.profile)
                .where(BusinessProfile.client_id == client.id)
                .order_by(BusinessProfile.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        summary, offerings = _profile_summary(profile)
        titles = (
            (
                await session.execute(
                    select(YtChannel.title).where(YtChannel.channel_id.in_(snapshot.competitor_channel_ids))
                )
            )
            .scalars()
            .all()
            if snapshot.competitor_channel_ids
            else []
        )
        return ClientSheet(
            client_id=client.id,
            name=client.name,
            website=client.website_url,
            summary=summary,
            offerings=offerings,
            buyer_questions=tuple(
                ref.label for ref in snapshot.topic_refs if ref.kind is TopicKind.buyer_question
            ),
            competitors=tuple(sorted(t for t in titles if t)),
            window_days=window_days,
        )
