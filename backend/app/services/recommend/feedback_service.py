import uuid
from datetime import datetime

from app.core.config import RecommendFeedbackSettings
from app.domain.recommendations import FeedbackStatus, RecommendationDraft
from app.services.recommend.feedback import FeedbackEffects, FeedbackReason, apply_feedback
from app.services.recommend.repository import RecommendationRepository


class UnknownRecommendationError(LookupError):
    pass


class FeedbackService:
    """save / done / dismiss (with reason) / snooze, keyed by the stable key so feedback survives daily runs."""

    def __init__(self, repository: RecommendationRepository, settings: RecommendFeedbackSettings) -> None:
        self.repository = repository
        self.settings = settings

    async def record(
        self,
        *,
        rec_id: str,
        user_id: uuid.UUID,
        status: FeedbackStatus | None,
        reason: FeedbackReason | None,
        now: datetime,
        linked_video_id: str | None = None,
        note: str | None = None,
    ) -> FeedbackEffects:
        if reason is not None and status is not FeedbackStatus.dismissed:
            raise ValueError("a reason is only recorded with a dismissal")
        row = await self.repository.latest_by_rec_id(rec_id)
        if row is None:
            raise UnknownRecommendationError(f"recommendation {rec_id} does not exist for this client")
        await self.repository.upsert_feedback(
            row=row,
            user_id=user_id,
            status=status,
            reason=reason,
            linked_video_id=linked_video_id,
            note=note,
            now=now,
        )
        config = await self.repository.config()
        if row.draft is None:
            # the evidence was redacted under the 30-day rule; the feedback itself is still kept
            return FeedbackEffects(config=config, proposals=())
        draft = RecommendationDraft.model_validate(row.draft)
        effects = apply_feedback(config, draft, status, reason, now, self.settings)
        await self.repository.save_config(effects.config, now)
        return effects
