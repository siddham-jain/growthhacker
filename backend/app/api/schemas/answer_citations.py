from typing import Literal

from pydantic import AwareDatetime, BaseModel

from app.api.schemas.results import VideoSummary
from app.services.answer_citations.lens import Owner, QuestionStatus

SOURCE_LABEL = "Web answer search (Tavily), videos resolved with the YouTube Data API"
DISCLOSURE = "Calculated by GrowthHacker, not a YouTube metric"


class CitedVideo(BaseModel):
    video: VideoSummary
    owner: Owner
    position: int | None
    cited_in: Literal["result", "answer"]


class QuestionCitations(BaseModel):
    buyer_question_id: str
    text: str
    funnel_stage: str
    checked_at: AwareDatetime
    status: QuestionStatus
    youtube_urls: int
    videos: list[CitedVideo]


class AnswerCitationCounts(BaseModel):
    # plain counts of checked questions; never blended with youtube view metrics
    questions_checked: int
    cite_you: int
    cite_competitors: int
    competitor_only: int
    no_tracked_channel: int


class ChannelCitations(BaseModel):
    channel_id: str
    channel_title: str | None
    buyer_question_ids: list[str]


class AnswerCitationsData(BaseModel):
    lens: Literal["conversion"] = "conversion"
    signal: Literal["cited_in_ai_answers"] = "cited_in_ai_answers"
    source_label: str = SOURCE_LABEL
    disclosure: str = DISCLOSURE
    window_days: int
    own_channel_id: str | None
    last_checked_at: AwareDatetime | None
    counts: AnswerCitationCounts
    questions: list[QuestionCitations]
    competitors: list[ChannelCitations]
