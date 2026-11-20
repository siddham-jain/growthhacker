from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.card_copy import CardCopy
from app.domain.common import DomainModel

# fixed positional aliases keep the compiled schema byte-stable across clients and runs
FactAlias = Literal["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"]
CardAlias = Literal["c1", "c2", "c3", "c4", "c5"]
CopyField = Literal[
    "summary", "working_title", "why_it_works_1", "why_it_works_2", "why_it_works_3", "fit_to_client"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReasonLine(StrictModel):
    fact_refs: list[FactAlias] = Field(min_length=1)
    text: str = Field(min_length=1)


class CardCopyLLM(StrictModel):
    """what the writer model fills; lengths and grounding are enforced by the validators."""

    summary: str = Field(min_length=1)
    working_title: str | None
    why_it_works: list[ReasonLine] = Field(min_length=1)
    fit_to_client: ReasonLine


class CritiqueScoresLLM(StrictModel):
    specific: int = Field(ge=1, le=5)
    evidenced: int = Field(ge=1, le=5)
    relevant: int = Field(ge=1, le=5)
    actionable: int = Field(ge=1, le=5)
    not_obvious: int = Field(ge=1, le=5)


class FieldCheckLLM(StrictModel):
    field: CopyField
    ok: bool


class CritiqueIssueLLM(StrictModel):
    field: CopyField
    problem: str
    fix_hint: str


class CardReviewLLM(StrictModel):
    card: CardAlias
    scores: CritiqueScoresLLM
    faithful: list[FieldCheckLLM]
    generic_phrases: list[str]
    issues: list[CritiqueIssueLLM]


class CritiqueBatchLLM(StrictModel):
    reviews: list[CardReviewLLM] = Field(min_length=1)


class WriterClientContext(DomainModel):
    """client facts the pack builder needs beyond the draft; assembled by the stage from 02/03/04 data."""

    name: str
    vertical: str
    language: str = "en"
    offerings: list[str] = []
    value_props: list[str] = []
    buyer_questions: dict[UUID, str] = {}
    locations: list[str] = []
    domains: list[str] = []
    recent_titles: list[str] = []
    known_channel_names: list[str] = []
    topic_labels: dict[str, str] = {}


class WrittenCard(DomainModel):
    draft_id: str
    stable_key: str
    card_copy: CardCopy
    critic_flags: list[str] = []


class WrittenCards(DomainModel):
    cards: list[WrittenCard] = []
    llm_cards: int = 0
    revised_cards: int = 0
    template_cards: int = 0
    skipped_suspicious_videos: int = 0
