import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.recommendations import (
    CreatorSubject,
    EvidenceSource,
    FeedbackStatus,
    RecommendationDraft,
    VideoIdeaSubject,
)
from app.models.recommendations import (
    ClientRecommendConfig as ConfigRow,
)
from app.models.recommendations import (
    CreatorCollaboration,
    RecommendationNotice,
    RecommendationOutcome,
    RecommendationReference,
    RecommendationSuppression,
)
from app.models.recommendations import (
    Recommendation as RecommendationRow,
)
from app.models.recommendations import (
    RecommendationFeedback as FeedbackRowModel,
)
from app.models.recommendations import (
    WhiteSpaceNote as WhiteSpaceRow,
)
from app.repositories.tenant.base import TenantRepository
from app.services.recommend.assembly import AssembledCard
from app.services.recommend.evidence.index import OwnVideo
from app.services.recommend.feedback import FeedbackRow, Outcome, OutcomeTracker, TrackedCard
from app.services.recommend.inputs import (
    ClientRecommendConfig,
    CreatorExclusion,
    FeedbackPenalty,
    PreviousCard,
)
from app.services.recommend.service import RecommendRun

CONFIG_OVERRIDE_KEYS = ("overrides", "feedback_penalties", "disabled_types", "creator_sort")


class RecommendationRepository(TenantRepository[RecommendationRow]):
    """07's tenant tables: shown cards with references, suppressions, notices, white space, feedback, outcomes."""

    model = RecommendationRow

    def _client_id(self) -> uuid.UUID:
        if self.scope.client_id is None:
            raise ValueError("recommendation storage needs a client-bound scope")
        return self.scope.client_id

    def _tenant(self) -> dict[str, Any]:
        return {"workspace_id": self.scope.workspace_id, "client_id": self._client_id()}

    async def save_run(self, run_id: uuid.UUID, run: RecommendRun) -> None:
        ranks = {
            card.id: position
            for window in run.output.windows
            for position, card in enumerate(window.set.recommendations, 1)
        }
        for card in run.cards:
            await self._save_card(run_id, card, ranks.get(card.recommendation.id))
        await self._expire_unsupported(run_id, {card.draft.stable_key for card in run.cards})
        await self._save_collaborations(run)
        for item in run.suppressed:
            self.session.add(
                RecommendationSuppression(
                    **self._tenant(),
                    run_id=run_id,
                    type=item.draft.type.value,
                    stable_key=item.draft.stable_key,
                    phase=item.phase,
                    gate=item.gate,
                    reason_code=item.reason_code,
                    details=dict(item.details),
                    draft=item.draft.model_dump(mode="json"),
                    created_at=item.created_at,
                )
            )
        for window in run.output.windows:
            for notice in window.set.notices:
                self.session.add(
                    RecommendationNotice(
                        **self._tenant(),
                        run_id=run_id,
                        window_days=notice.window_days,
                        kind=notice.kind,
                        rec_type=notice.rec_type.value if notice.rec_type else None,
                        payload=notice.model_dump(mode="json"),
                    )
                )
            for note in window.set.white_space:
                self.session.add(
                    WhiteSpaceRow(
                        **self._tenant(),
                        run_id=run_id,
                        buyer_question_id=note.buyer_question_id,
                        payload=note.model_dump(mode="json"),
                    )
                )
        await self.session.flush()

    async def _expire_unsupported(self, run_id: uuid.UUID, shown: set[str]) -> None:
        # yesterday's cards that no longer pass today's gates expire; their suppression row says why
        statement = (
            update(RecommendationRow)
            .where(
                RecommendationRow.workspace_id == self.scope.workspace_id,
                RecommendationRow.client_id == self._client_id(),
                RecommendationRow.status == "shown",
                RecommendationRow.run_id != run_id,
                RecommendationRow.stable_key.not_in(shown) if shown else true(),
            )
            .values(status="expired")
        )
        await self.session.execute(statement)

    async def _save_collaborations(self, run: RecommendRun) -> None:
        rows = {
            (subject.channel_id, item.competitor_channel_id, item.video_id, item.kind): {
                "creator_channel_id": subject.channel_id,
                "other_channel_id": item.competitor_channel_id,
                "video_id": item.video_id,
                "kind": item.kind,
                "matched_on": item.matched_on,
                "matched_token": item.matched_token,
                "video_published_at": item.published_at,
            }
            for card in run.cards
            if isinstance(subject := card.draft.subject, CreatorSubject)
            for item in subject.collaborations
        }
        if rows:
            statement = pg_insert(CreatorCollaboration).values(list(rows.values())).on_conflict_do_nothing()
            await self.session.execute(statement)

    async def _save_card(self, run_id: uuid.UUID, card: AssembledCard, rank: int | None) -> None:
        recommendation, draft, meta = card.recommendation, card.draft, card.meta
        uses_authorized = any(
            reason.source is EvidenceSource.youtube_own_analytics
            for assignment in draft.lenses
            for reason in assignment.reasons
        )
        values = {
            **self._tenant(),
            "rec_id": recommendation.id,
            "stable_key": draft.stable_key,
            "run_id": run_id,
            "type": draft.type.value,
            "window_days": draft.window_days,
            "rank": rank,
            "status": "shown",
            "lens": [lens.value for lens in recommendation.lens],
            "buyer_question_id": draft.buyer_question_id,
            "score_total": draft.score.total if draft.score else None,
            "score_components": draft.score.model_dump(mode="json") if draft.score else {},
            "payload": recommendation.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "writer_meta": {
                "rendered_by": meta.rendered_by,
                "attempts": meta.attempts,
                "evidence_hash": meta.evidence_hash,
                "unchanged_since": meta.unchanged_since.isoformat(),
            },
            "referenced_video_ids": [ref.video_id for ref in recommendation.reference_videos],
            "referenced_channel_ids": sorted({ref.channel_id for ref in recommendation.reference_videos}),
            "uses_authorized_data": uses_authorized,
            "first_seen_at": meta.first_seen_at,
            "last_seen_at": recommendation.generated_at,
            "generated_at": recommendation.generated_at,
            "api_data_observed_at": draft.as_of,
            "expires_at": recommendation.extras.expires_at,
        }
        row_id = (
            await self.session.execute(
                insert(RecommendationRow).values(**values).returning(RecommendationRow.id)
            )
        ).scalar_one()
        for ordinal, ref in enumerate(recommendation.reference_videos, 1):
            self.session.add(
                RecommendationReference(
                    **self._tenant(),
                    recommendation_id=row_id,
                    ordinal=ordinal,
                    video_id=ref.video_id,
                    channel_id=ref.channel_id,
                    channel_role=ref.channel_role.value,
                )
            )

    async def previous_cards(self, window_days: int) -> dict[str, PreviousCard]:
        latest = (
            select(
                RecommendationRow.stable_key, func.max(RecommendationRow.generated_at).label("generated_at")
            )
            .where(RecommendationRow.window_days == window_days, RecommendationRow.status == "shown")
            .group_by(RecommendationRow.stable_key)
        )
        latest = self._scoped(latest).subquery()
        statement = self._scoped(
            select(RecommendationRow).join(
                latest,
                (RecommendationRow.stable_key == latest.c.stable_key)
                & (RecommendationRow.generated_at == latest.c.generated_at),
            )
        )
        rows = (await self.session.execute(statement)).scalars().all()
        acted = await self._keys_with_feedback()
        previous = {}
        for row in rows:
            meta = row.writer_meta or {}
            if "evidence_hash" not in meta:
                continue
            previous[row.stable_key] = PreviousCard(
                stable_key=row.stable_key,
                first_seen_at=row.first_seen_at,
                unchanged_since=datetime.fromisoformat(meta["unchanged_since"]),
                evidence_hash=meta["evidence_hash"],
                acted=row.stable_key in acted,
            )
        return previous

    async def _keys_with_feedback(self) -> set[str]:
        statement = select(FeedbackRowModel.stable_key).where(
            FeedbackRowModel.workspace_id == self.scope.workspace_id,
            FeedbackRowModel.client_id == self._client_id(),
        )
        return set((await self.session.execute(statement)).scalars().all())

    async def latest_by_rec_id(self, rec_id: str) -> RecommendationRow | None:
        statement = self._scoped(
            select(RecommendationRow)
            .where(RecommendationRow.rec_id == rec_id)
            .order_by(RecommendationRow.generated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def upsert_feedback(
        self,
        *,
        row: RecommendationRow,
        user_id: uuid.UUID,
        status: FeedbackStatus | None,
        reason: str | None,
        linked_video_id: str | None,
        note: str | None,
        now: datetime,
    ) -> None:
        values = {
            **self._tenant(),
            "recommendation_id": row.id,
            "stable_key": row.stable_key,
            "user_id": user_id,
            "status": status.value if status else None,
            "reason": reason,
            "linked_video_id": linked_video_id,
            "linked_video_verified_at": now if linked_video_id else None,
            "note": note,
        }
        statement = pg_insert(FeedbackRowModel).values(**values, created_at=now, updated_at=now)
        changes = {key: statement.excluded[key] for key in values if key not in ("workspace_id", "client_id")}
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "stable_key", "user_id"], set_={**changes, "updated_at": now}
        )
        await self.session.execute(statement)

    async def feedback_rows(self) -> list[FeedbackRow]:
        statement = select(FeedbackRowModel).where(
            FeedbackRowModel.workspace_id == self.scope.workspace_id,
            FeedbackRowModel.client_id == self._client_id(),
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            FeedbackRow(
                stable_key=row.stable_key,
                status=FeedbackStatus(row.status) if row.status else None,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def config(self) -> ClientRecommendConfig:
        row = await self.session.get(ConfigRow, self._client_id())
        if row is None or row.workspace_id != self.scope.workspace_id:
            return ClientRecommendConfig()
        stored = row.recommend_overrides or {}
        return ClientRecommendConfig(
            lens_mode=row.lens_mode,
            overrides=stored.get("overrides", {}),
            excluded_formats=list(row.excluded_formats),
            excluded_creators=[CreatorExclusion.model_validate(item) for item in row.excluded_creators],
            feedback_penalties=[
                FeedbackPenalty.model_validate(item) for item in stored.get("feedback_penalties", [])
            ],
            disabled_types=stored.get("disabled_types", []),
            creator_sort=stored.get("creator_sort"),
        )

    async def save_config(self, config: ClientRecommendConfig, now: datetime) -> None:
        dumped = config.model_dump(mode="json")
        values = {
            **self._tenant(),
            "lens_mode": config.lens_mode,
            "recommend_overrides": {key: dumped[key] for key in CONFIG_OVERRIDE_KEYS},
            "excluded_formats": list(config.excluded_formats),
            "excluded_creators": dumped["excluded_creators"],
        }
        statement = pg_insert(ConfigRow).values(**values, created_at=now, updated_at=now)
        changes = {key: statement.excluded[key] for key in values if key not in ("workspace_id", "client_id")}
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=["client_id"], set_={**changes, "updated_at": now})
        )

    async def tracked_cards(self, since: datetime) -> list[TrackedCard]:
        # shown video ideas whose evidence is still stored, plus any video the user linked when marking done
        statement = self._scoped(
            select(RecommendationRow).where(
                RecommendationRow.type == "video_idea",
                RecommendationRow.first_seen_at >= since,
                RecommendationRow.draft.is_not(None),
            )
        )
        rows = (await self.session.execute(statement)).scalars().all()
        links = await self._linked_videos()
        tracked: dict[str, TrackedCard] = {}
        for row in sorted(rows, key=lambda item: item.generated_at):
            draft = RecommendationDraft.model_validate(row.draft)
            subject = draft.subject
            assert isinstance(subject, VideoIdeaSubject)
            expected = draft.expected_range
            tracked[row.stable_key] = TrackedCard(
                stable_key=row.stable_key,
                topic_ids=tuple(draft.topic_ids),
                format_key=subject.format_key,
                first_seen_at=row.first_seen_at,
                predicted_low=expected.low if expected else None,
                predicted_high=expected.high if expected else None,
                comparison_age_days=expected.comparison_age_days if expected else 7,
                linked_video_id=links.get(row.stable_key),
            )
        return list(tracked.values())

    async def _linked_videos(self) -> dict[str, str]:
        statement = select(FeedbackRowModel.stable_key, FeedbackRowModel.linked_video_id).where(
            FeedbackRowModel.workspace_id == self.scope.workspace_id,
            FeedbackRowModel.client_id == self._client_id(),
            FeedbackRowModel.linked_video_id.is_not(None),
        )
        return {key: video_id for key, video_id in (await self.session.execute(statement)).all()}

    async def measured_keys(self) -> set[str]:
        statement = select(RecommendationOutcome.stable_key).where(
            RecommendationOutcome.workspace_id == self.scope.workspace_id,
            RecommendationOutcome.client_id == self._client_id(),
        )
        return set((await self.session.execute(statement)).scalars().all())

    async def save_outcomes(self, outcomes: Sequence[Outcome], measured_at: datetime) -> None:
        for outcome in outcomes:
            self.session.add(
                RecommendationOutcome(
                    **self._tenant(),
                    stable_key=outcome.stable_key,
                    video_id=outcome.video_id,
                    video_verified_at=measured_at,
                    matched_by=outcome.matched_by,
                    comparison_age_days=outcome.comparison_age_days,
                    predicted_low=outcome.predicted_low,
                    predicted_high=outcome.predicted_high,
                    observed_ratio=outcome.observed_ratio,
                    within_range=outcome.within_range,
                    became_outlier=outcome.became_outlier,
                    measured_at=measured_at,
                )
            )
        await self.session.flush()


class SqlRecommendationStore:
    """persists one run's cards, suppressions and notices, and the outcomes of cards the client made."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], outcome_tracker: OutcomeTracker) -> None:
        self.uow_factory = uow_factory
        self.outcome_tracker = outcome_tracker

    async def save_run(
        self, scope: TenantScope, run_id: uuid.UUID, run: RecommendRun, own: list[OwnVideo]
    ) -> int:
        async with self.uow_factory() as uow:
            repository = uow.repository(RecommendationRepository, scope)
            await repository.save_run(run_id, run)
            since = run.output.as_of - timedelta(days=self.outcome_tracker.auto_done_days * 2)
            tracked = await repository.tracked_cards(since)
            outcomes = self.outcome_tracker.measure(tracked, own, await repository.measured_keys())
            await repository.save_outcomes(outcomes, run.output.as_of)
            await uow.commit()
        return len(outcomes)
