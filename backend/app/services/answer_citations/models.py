from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from app.domain.common import ChannelId, DomainModel, VideoId

CitedIn = Literal["result", "answer"]


class AnswerSource(DomainModel):
    url: str
    position: int = Field(ge=1)


class AnswerSearchResult(DomainModel):
    answer: str | None
    sources: list[AnswerSource]
    credits_used: float = Field(ge=0)
    from_cache: bool


class AnswerSearch(Protocol):
    # one web answer search; sources are the pages the answer was written from, in rank order
    async def answer(
        self, question: str, *, max_results: int, country: str | None, client_id: UUID | None
    ) -> AnswerSearchResult: ...


class QuestionRef(DomainModel):
    id: UUID
    text: str


class ResolvedCitation(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    cited_url: str
    position: int | None
    cited_in: CitedIn


class CitationCheck(DomainModel):
    question_id: UUID
    checked_at: datetime
    result_count: int
    youtube_url_count: int
    unresolved_count: int
    answer_returned: bool
    credits: float
    from_cache: bool
    citations: list[ResolvedCitation]
