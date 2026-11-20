from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.llm import RecommendationCopy
from app.services.llm.writer.schemas import WrittenCard

UPDATABLE = (
    "run_id",
    "copy_source",
    "degrade_reason",
    "prompt_id",
    "prompt_version",
    "model",
    "attempts",
    "reason_fact_ids",
    "fit_fact_ids",
    "violations_last",
    "critique",
    "language",
)


class RecommendationCopyStore:
    """provenance of every card's wording (fact refs, copy source, critique); text itself lives on the card."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def save(self, scope: TenantScope, run_id: UUID, cards: list[WrittenCard]) -> None:
        if scope.client_id is None:
            raise ValueError("recommendation copy is client-scoped")
        if not cards:
            return
        rows = []
        for card in cards:
            copy = card.card_copy
            rows.append(
                {
                    "workspace_id": scope.workspace_id,
                    "client_id": scope.client_id,
                    "draft_id": card.draft_id,
                    "run_id": run_id,
                    "copy_source": copy.copy_source,
                    "degrade_reason": copy.degrade_reason,
                    "prompt_id": copy.prompt_id,
                    "prompt_version": copy.prompt_version,
                    "model": copy.model,
                    "attempts": copy.attempts,
                    "reason_fact_ids": copy.reason_fact_ids,
                    "fit_fact_ids": copy.fit_fact_ids,
                    "violations_last": [violation.model_dump() for violation in copy.violations_last],
                    "critique": copy.critique.model_dump() if copy.critique else None,
                    "language": copy.language,
                }
            )
        statement = insert(RecommendationCopy).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[RecommendationCopy.draft_id],
            set_={name: statement.excluded[name] for name in UPDATABLE},
            # a draft id never moves between tenants
            where=(RecommendationCopy.client_id == statement.excluded.client_id),
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def get(self, scope: TenantScope, draft_id: str) -> RecommendationCopy | None:
        query = select(RecommendationCopy).where(
            RecommendationCopy.workspace_id == scope.workspace_id, RecommendationCopy.draft_id == draft_id
        )
        if scope.client_id is not None:
            query = query.where(RecommendationCopy.client_id == scope.client_id)
        async with self._session_factory() as session:
            return await session.scalar(query)
