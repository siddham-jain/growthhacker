from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.review.models import (
    GateProposal,
    Rating,
    ReviewCard,
    ReviewItemRecord,
    ReviewSampleDesign,
)
from app.models.evaluation import REVIEW_CRITERIA, GateTuningProposal, ReviewItem, ReviewRating


def _record(row: ReviewItem) -> ReviewItemRecord:
    snapshot = row.card_snapshot or {}
    card = snapshot.get("card")
    return ReviewItemRecord(
        item_id=row.item_id,
        batch_id=row.batch_id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        is_decoy=row.is_decoy,
        rec_id=snapshot.get("rec_id"),
        type=snapshot.get("type", "unknown"),
        lens=tuple(snapshot.get("lens", ())),
        confidence_label=snapshot.get("confidence_label", "unknown"),
        card=ReviewCard.model_validate(card) if card else None,
        gate_features={k: float(v) for k, v in (row.gate_features or {}).items()},
    )


class ReviewStore:
    """review_item, review_rating and gate_tuning_proposal; the caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_design(self, design: ReviewSampleDesign) -> int:
        for item in design.items:
            source = item.source
            self.session.add(
                ReviewItem(
                    item_id=item.item_id,
                    batch_id=item.batch_id,
                    recommendation_id=source.db_id,
                    workspace_id=item.workspace_id,
                    client_id=item.client_id,
                    card_snapshot={
                        "rec_id": source.rec_id,
                        "type": source.type,
                        "lens": list(source.lens),
                        "confidence_label": source.confidence_label,
                        "card": source.card.model_dump(mode="json"),
                    },
                    is_decoy=item.is_decoy,
                    gate_features=source.gate_features,
                    display_seed=item.display_seed,
                    expires_at=item.expires_at,
                )
            )
        await self.session.flush()
        return len(design.items)

    async def latest_batch_id(self, workspace_id: UUID | None = None) -> UUID | None:
        statement = select(ReviewItem.batch_id).order_by(ReviewItem.batch_id.desc()).limit(1)
        if workspace_id is not None:
            statement = statement.where(ReviewItem.workspace_id == workspace_id)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def items(self, batch_id: UUID, workspace_id: UUID | None = None) -> list[ReviewItemRecord]:
        statement = select(ReviewItem).where(ReviewItem.batch_id == batch_id)
        if workspace_id is not None:
            statement = statement.where(ReviewItem.workspace_id == workspace_id)
        rows = (await self.session.execute(statement)).scalars().all()
        return [_record(row) for row in sorted(rows, key=lambda r: str(r.item_id))]

    async def save_ratings(self, ratings: Sequence[Rating]) -> int:
        for rating in ratings:
            values = {
                "item_id": rating.item_id,
                "rater_id": rating.rater_id,
                **{c: getattr(rating, c) for c in REVIEW_CRITERIA},
                "would_ship": rating.would_ship,
                "comment": rating.comment,
                "rated_at": rating.rated_at,
            }
            statement = insert(ReviewRating).values(**values)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ReviewRating.item_id, ReviewRating.rater_id],
                    set_={
                        name: statement.excluded[name]
                        for name in (*REVIEW_CRITERIA, "would_ship", "comment", "rated_at")
                    },
                )
            )
        return len(ratings)

    async def ratings(self, item_ids: Sequence[UUID]) -> list[Rating]:
        if not item_ids:
            return []
        rows = (
            (await self.session.execute(select(ReviewRating).where(ReviewRating.item_id.in_(list(item_ids)))))
            .scalars()
            .all()
        )
        return [
            Rating(
                item_id=r.item_id,
                rater_id=r.rater_id,
                **{c: getattr(r, c) for c in REVIEW_CRITERIA},
                would_ship=r.would_ship,
                comment=r.comment,
                rated_at=r.rated_at,
            )
            for r in sorted(rows, key=lambda r: (str(r.item_id), str(r.rater_id)))
        ]

    async def save_proposals(self, batch_id: UUID, proposals: Sequence[GateProposal]) -> int:
        for proposal in proposals:
            self.session.add(
                GateTuningProposal(
                    batch_id=batch_id,
                    feature=proposal.feature,
                    rho=proposal.spearman_rho,
                    current_threshold=proposal.current_threshold,
                    proposed_threshold=proposal.proposed_threshold,
                    retained_share=proposal.retained_share,
                    retained_mean_rating=proposal.retained_mean_rating,
                    config_diff=proposal.config_diff,
                    rationale=proposal.rationale,
                )
            )
        await self.session.flush()
        return len(proposals)
