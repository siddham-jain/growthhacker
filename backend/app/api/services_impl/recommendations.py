from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.api import ids
from app.api.errors import FieldError, NotFound, ValidationFailed
from app.api.pagination import PageRequest
from app.api.schemas.results import FeedbackIn, RecommendationOut
from app.api.services import (
    RECOMMENDATIONS,
    RecommendationFilters,
    RecommendationListing,
    RecommendationLookup,
    api_service_registry,
)
from app.api.services_impl.common import ArtifactNotStored, ServiceBase
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.card_copy import CardCopy
from app.domain.recommendations import (
    ASSIGNMENT_REC_TYPES,
    FeedbackStatus,
    Recommendation,
    RecommendationFeedback,
    RecType,
    recommendation_id,
)
from app.models.recommendations import Recommendation as RecommendationRow
from app.models.recommendations import RecommendationFeedback as FeedbackRow
from app.models.runs import PipelineRun
from app.pipeline.stages.cards import WRITTEN_CARDS
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.services.recommend.assembly import reworded, with_copy
from app.services.recommend.feedback_service import FeedbackService, UnknownRecommendationError
from app.services.recommend.repository import RecommendationRepository
from app.services.recommend.service import WindowOutput

# without a status filter, cards the user put away stay out of the list
HIDDEN_BY_DEFAULT = frozenset({FeedbackStatus.dismissed, FeedbackStatus.snoozed})


def feedback_domain(row: FeedbackRow) -> RecommendationFeedback:
    return RecommendationFeedback(
        id=row.id,
        client_id=row.client_id,
        recommendation_id=recommendation_id(row.stable_key),
        stable_key=row.stable_key,
        user_id=row.user_id,
        status=FeedbackStatus(row.status) if row.status else None,
        reason=row.reason,  # type: ignore[arg-type]
        linked_video_id=row.linked_video_id,
        note=row.note,
        created_at=row.created_at,
    )


class RecommendationReadService(ServiceBase):
    """07's published card set for one window, with 10's wording and the caller's own feedback."""

    async def window_output(self, scope: TenantScope, run_id: UUID, window_days: int) -> WindowOutput:
        output = await self.artifact(scope, run_id, RECOMMEND_ARTIFACT)
        window = output.window(window_days)
        if window is None:
            raise ArtifactNotStored(f"the published run has no {window_days}-day recommendations")
        return window

    async def copies(self, scope: TenantScope, run_id: UUID) -> dict[str, CardCopy]:
        written = await self.optional_artifact(scope, run_id, WRITTEN_CARDS)
        return {card.stable_key: card.card_copy for card in written.cards} if written else {}

    async def worded(
        self, scope: TenantScope, run_id: UUID, cards: list[Recommendation]
    ) -> list[Recommendation]:
        """each card with the llm wording attached to its row, then the published run's own written copy."""
        copies = await self.copies(scope, run_id)
        attached = await self._attached(scope, cards)
        return [
            with_copy(
                attached.get((card.extras.run_id, card.extras.stable_key), card),
                copies.get(card.extras.stable_key),
            )
            for card in cards
        ]

    async def _attached(
        self, scope: TenantScope, cards: list[Recommendation]
    ) -> dict[tuple[UUID, str], Recommendation]:
        # a later cards run words template cards on the rows of the run that saved them
        pending = {
            (card.extras.run_id, card.extras.stable_key): card
            for card in cards
            if card.extras.rendered_by == "template"
        }
        if not pending:
            return {}
        payload = RecommendationRow.payload
        async with self.session_factory() as session:
            rows = await session.execute(
                select(
                    RecommendationRow.run_id,
                    RecommendationRow.stable_key,
                    payload["summary"].astext,
                    payload["why_it_works"],
                    payload["fit_to_client"].astext,
                ).where(
                    RecommendationRow.workspace_id == scope.workspace_id,
                    RecommendationRow.client_id == scope.client_id,
                    RecommendationRow.run_id.in_({run for run, _ in pending}),
                    RecommendationRow.stable_key.in_({key for _, key in pending}),
                    RecommendationRow.writer_meta["rendered_by"].astext == "llm",
                    payload.is_not(None),
                )
            )
            return {
                (run, key): reworded(
                    pending[(run, key)],
                    summary=summary,
                    why_it_works=list(why_it_works),
                    fit_to_client=fit_to_client,
                    rendered_by="llm",
                )
                for run, key, summary, why_it_works, fit_to_client in rows
                if (run, key) in pending
            }

    async def cards_written(self, scope: TenantScope, run_id: UUID, window_days: int) -> bool:
        window = await self.window_output(scope, run_id, window_days)
        async with self.session_factory() as session:
            run = await session.get(PipelineRun, run_id)
        if (
            run is not None
            and ((run.config_snapshot or {}).get("window_days") or run.window_days) == window_days
        ):
            return True
        keys = {card.extras.stable_key for card in window.set.recommendations}
        return keys <= set(await self.copies(scope, run_id))

    async def _feedback(
        self, scope: TenantScope, user_id: UUID | None, keys: set[str]
    ) -> dict[str, FeedbackRow]:
        if user_id is None or not keys:
            return {}
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(FeedbackRow).where(
                        FeedbackRow.workspace_id == scope.workspace_id,
                        FeedbackRow.client_id == scope.client_id,
                        FeedbackRow.user_id == user_id,
                        FeedbackRow.stable_key.in_(keys),
                    )
                )
            ).scalars()
            return {row.stable_key: row for row in rows}

    async def list_cards(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        filters: RecommendationFilters,
        page: PageRequest,
        user_id: UUID | None,
    ) -> RecommendationListing:
        window = await self.window_output(scope, run_id, window_days)
        # description refreshes are their own surface; they join the list only when asked for by type
        pool = (
            window.refresh_cards
            if filters.type == RecType.description_refresh
            else window.set.recommendations
        )
        cards = await self.worded(scope, run_id, pool)
        feedback = await self._feedback(scope, user_id, {card.extras.stable_key for card in cards})
        question = (
            ids.decode(ids.BUYER_QUESTION, filters.buyer_question_id) if filters.buyer_question_id else None
        )

        def keep(card: Recommendation) -> bool:
            row = feedback.get(card.extras.stable_key)
            status = FeedbackStatus(row.status) if row is not None and row.status else None
            if filters.type is not None and card.type != filters.type:
                return False
            if filters.assignment_types_only and card.type not in ASSIGNMENT_REC_TYPES:
                return False
            if filters.lens is not None and filters.lens not in card.lens:
                return False
            if question is not None and card.extras.buyer_question_id != question:
                return False
            if filters.confidence is not None and card.confidence.label != filters.confidence:
                return False
            if filters.status is not None:
                return status is not None and status.value == filters.status
            return status not in HIDDEN_BY_DEFAULT

        items = [
            RecommendationOut(
                recommendation=card,
                feedback=feedback_domain(feedback[card.extras.stable_key])
                if card.extras.stable_key in feedback
                else None,
            )
            for card in cards
            if keep(card)
        ]
        key = {"run": str(run_id), "window": window_days, "filters": repr(filters), "user": str(user_id)}
        chunk = self.paginate(items, page, key)
        return RecommendationListing(
            items=chunk.items,
            next_cursor=chunk.next_cursor,
            notices=window.set.notices,
            white_space=window.set.white_space,
            suppressed_counts=window.set.suppressed_counts,
        )

    async def _row(self, scope: TenantScope, rec_id: str) -> RecommendationRow:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(RecommendationRow)
                    .where(
                        RecommendationRow.workspace_id == scope.workspace_id,
                        RecommendationRow.rec_id == rec_id,
                    )
                    .order_by(RecommendationRow.generated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            raise NotFound("recommendation not found")
        return row

    async def get(
        self, scope: TenantScope, recommendation_id: str, user_id: UUID | None
    ) -> RecommendationLookup:
        row = await self._row(scope, recommendation_id)
        client_scope = scope.for_client(row.client_id)
        try:
            card = Recommendation.model_validate(row.payload)
        except ValidationError:
            # the 30-day rule redacted this card's evidence; only its summary and feedback remain
            raise NotFound("this recommendation expired and its evidence was removed") from None
        card = with_copy(card, (await self.copies(client_scope, row.run_id)).get(row.stable_key))
        feedback = await self._feedback(client_scope, user_id, {row.stable_key})
        return RecommendationLookup(
            client_id=row.client_id,
            run_id=row.run_id,
            item=RecommendationOut(
                recommendation=card,
                feedback=feedback_domain(feedback[row.stable_key]) if row.stable_key in feedback else None,
            ),
        )

    async def put_feedback(
        self, scope: TenantScope, recommendation_id: str, user_id: UUID, body: FeedbackIn
    ) -> RecommendationFeedback:
        row = await self._row(scope, recommendation_id)
        client_scope = scope.for_client(row.client_id)
        async with self.container.uow() as uow:
            service = FeedbackService(
                uow.repository(RecommendationRepository, client_scope), self.settings.recommend.feedback
            )
            try:
                await service.record(
                    rec_id=recommendation_id,
                    user_id=user_id,
                    status=FeedbackStatus(body.status) if body.status else None,
                    reason=body.reason,
                    now=self.clock.now(),
                    linked_video_id=body.linked_video_id,
                    note=body.note,
                )
            except UnknownRecommendationError:
                raise NotFound("recommendation not found") from None
            except ValueError as error:
                raise ValidationFailed(
                    str(error), errors=[FieldError(loc=["body", "reason"], msg=str(error), type="invalid")]
                ) from None
            await uow.commit()
        stored = await self._feedback(client_scope, user_id, {row.stable_key})
        return feedback_domain(stored[row.stable_key])


@api_service_registry.register(RECOMMENDATIONS)
def build_recommendation_service(container: Container) -> RecommendationReadService:
    return RecommendationReadService(container)
