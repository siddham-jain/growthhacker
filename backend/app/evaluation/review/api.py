from typing import Any

from app.api.errors import NotFound, ValidationFailed
from app.api.schemas.admin import ReviewIn
from app.core.tenancy import TenantScope
from app.evaluation.packs.participants import participant_id
from app.evaluation.review.models import Rating, ReviewItemRecord
from app.evaluation.review.scorer import summarise_review
from app.evaluation.review.store import ReviewStore
from app.models.evaluation import REVIEW_CRITERIA


def _matches(item: ReviewItemRecord, key: str) -> bool:
    return str(item.item_id) == key or (item.rec_id is not None and item.rec_id == key)


class ReviewApi:
    """the workspace's latest review batch for 11's /evaluation/reviews routes; raters stay blind."""

    def __init__(self, container: Any) -> None:
        self.session_factory = container.session_factory
        self.settings = container.settings
        self.clock = container.clock

    async def review_queue(self, scope: TenantScope) -> dict[str, Any]:
        async with self.session_factory() as session:
            store = ReviewStore(session)
            batch_id = await store.latest_batch_id(scope.workspace_id)
            if batch_id is None:
                return {"batch_id": None, "items": [], "rated": {}}
            items = [i for i in await store.items(batch_id, scope.workspace_id) if i.card is not None]
            ratings = await store.ratings([i.item_id for i in items])
        rated: dict[str, int] = {}
        for rating in ratings:
            rated[str(rating.item_id)] = rated.get(str(rating.item_id), 0) + 1
        return {
            "batch_id": str(batch_id),
            "items": [
                {
                    "item_id": str(i.item_id),
                    "client_id": str(i.client_id),
                    "card": i.card.model_dump(mode="json"),
                }
                for i in items
                if i.card is not None
            ],
            "rated": rated,
            "rubric": list(REVIEW_CRITERIA),
        }

    async def submit_review(self, scope: TenantScope, body: ReviewIn) -> dict[str, Any]:
        # recommendation_id accepts the queue's item_id, which also covers the blinded decoys
        now = self.clock.now()
        async with self.session_factory() as session, session.begin():
            store = ReviewStore(session)
            batch_id = await store.latest_batch_id(scope.workspace_id)
            items = [] if batch_id is None else await store.items(batch_id, scope.workspace_id)
            item = next((i for i in items if _matches(i, body.recommendation_id)), None)
            if item is None:
                raise NotFound("this card is not in the workspace's current review batch")
            if item.card is None:
                raise ValidationFailed("this review item expired under the data policy")
            rating = Rating(
                item_id=item.item_id,
                rater_id=participant_id(body.reviewer),
                **{c: getattr(body, c) for c in REVIEW_CRITERIA},
                would_ship=body.would_ship,
                comment=body.comment,
                rated_at=now,
            )
            await store.save_ratings([rating])
        return {"item_id": str(item.item_id), "rater_id": str(rating.rater_id), "rated_at": now.isoformat()}

    async def review_summary(self, scope: TenantScope) -> dict[str, Any]:
        async with self.session_factory() as session:
            store = ReviewStore(session)
            batch_id = await store.latest_batch_id(scope.workspace_id)
            if batch_id is None:
                return {"batch_id": None, "n_items": 0}
            items = await store.items(batch_id, scope.workspace_id)
            ratings = await store.ratings([i.item_id for i in items])
        summary = summarise_review(
            batch_id, items, ratings, self.settings.evaluation.review, self.settings.evaluation.seed
        )
        return summary.model_dump(mode="json")
