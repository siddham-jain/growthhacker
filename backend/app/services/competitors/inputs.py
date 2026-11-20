from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import BuyerQuestion as BuyerQuestionRow

MAX_BUYER_QUESTIONS = 10


async def latest_profile(session: AsyncSession, scope: TenantScope) -> BusinessProfile | None:
    # a confirmed profile wins over newer drafts
    statement = (
        select(ProfileRow)
        .where(ProfileRow.workspace_id == scope.workspace_id, ProfileRow.client_id == scope.client_id)
        .order_by(case((ProfileRow.status == "confirmed", 0), else_=1), ProfileRow.version.desc())
        .limit(1)
    )
    row = (await session.execute(statement)).scalar_one_or_none()
    return BusinessProfile.model_validate(row.profile) if row is not None else None


async def buyer_question_texts(session: AsyncSession, scope: TenantScope) -> list[str]:
    statement = (
        select(BuyerQuestionRow.text)
        .where(
            BuyerQuestionRow.workspace_id == scope.workspace_id,
            BuyerQuestionRow.client_id == scope.client_id,
            BuyerQuestionRow.status.notin_(("rejected", "retired")),
        )
        .order_by(BuyerQuestionRow.priority, BuyerQuestionRow.text)
        .limit(MAX_BUYER_QUESTIONS)
    )
    return list((await session.execute(statement)).scalars().all())
