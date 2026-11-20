from typing import Protocol
from uuid import UUID

from app.domain.common import DomainModel
from app.domain.profile import (
    BusinessProfile,
    BuyerQuestionIntent,
    BuyerQuestionSourceKind,
    Evidence,
    FunnelStage,
)


class BuyerQuestionCandidate(DomainModel):
    text: str
    language: str
    source: BuyerQuestionSourceKind
    source_ref: str | None = None
    funnel_stage: FunnelStage | None = None
    intent: BuyerQuestionIntent | None = None
    offering_ids: list[str] = []
    basis_ids: list[str] = []
    evidence: list[Evidence] = []
    ai_cited_video_ids: list[str] | None = None


class BuyerQuestionSource(Protocol):
    # higher precedence wins on duplicates: user 100 > spotlit 80 > website_faq 60 > derived 40
    source_id: BuyerQuestionSourceKind
    precedence: int

    def is_enabled(self, client_id: UUID) -> bool: ...

    async def fetch(self, client_id: UUID, profile: BusinessProfile) -> list[BuyerQuestionCandidate]: ...
