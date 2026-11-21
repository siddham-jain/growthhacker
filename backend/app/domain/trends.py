from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from app.domain.common import ChannelId, DomainModel, VideoId
from app.domain.topics import TopicRefId


class KeywordSource(StrEnum):
    profile = "profile"
    autocomplete = "autocomplete"
    web = "web"


class KeywordCandidate(DomainModel):
    keyword: str
    sources: list[KeywordSource]
    # every candidate stays anchored to one of the client's own topics
    topic_ref_id: TopicRefId
    topic_label: str
    # position in youtube's suggestion list for the seed, 0 = first
    autocomplete_rank: int | None = None
    web_reason: str | None = None
    web_urls: list[str] = []


class TrendVideo(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    channel_title: str
    title: str
    published_at: AwareDatetime
    views: int
    age_days: float
    views_per_day: float
    duration_s: int | None = None
    thumbnail_url: str | None = None
    is_competitor: bool = False


Heat = Literal["hot", "warm", "quiet"]
CheckStatus = Literal["checked", "cached", "unchecked_quota", "unchecked_error"]


class KeywordTrend(DomainModel):
    candidate: KeywordCandidate
    status: CheckStatus
    checked_at: AwareDatetime
    window_days: int
    recent_videos: int = 0
    channels: int = 0
    competitor_videos: int = 0
    median_views_per_day: float | None = None
    top_views_per_day: float | None = None
    # median views/day of the five fastest recent videos; what heat compares
    leaders_views_per_day: float | None = None
    # median views/day now vs the previous scan of the same keyword, when there is one
    momentum: float | None = None
    heat: Heat = "quiet"
    videos: list[TrendVideo] = []


class TrendIdea(DomainModel):
    id: str
    keyword: str
    topic_ref_id: TopicRefId
    title: str
    angle: str
    fit_to_client: str
    format: Literal["short", "long"]
    # evidence lines are written by code from the keyword's measured numbers, never by the llm
    why: list[str] = Field(min_length=1)
    reference_video_ids: list[VideoId] = Field(min_length=1)
    buyer_question: str | None = None


class TrendScan(DomainModel):
    as_of: AwareDatetime
    window_days: int
    region: str | None = None
    language: str | None = None
    keywords: list[KeywordTrend] = []
    ideas: list[TrendIdea] = []
    search_calls: int = 0
    youtube_units: int = 0
    llm_calls: int = 0
    tavily_credits: float = 0.0
    messages: list[str] = []
