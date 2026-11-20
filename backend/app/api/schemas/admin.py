from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas.common import ApiModel, WindowDays


class LoginIn(ApiModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    password: str = Field(min_length=1, max_length=512)


class WorkspaceSwitchIn(ApiModel):
    workspace_id: str


class MeWorkspace(BaseModel):
    id: str
    name: str
    slug: str
    role: Literal["owner", "editor", "viewer"]


class MeUser(BaseModel):
    id: str
    email: str
    display_name: str | None = None


class MeOut(BaseModel):
    principal: Literal["user", "api_key"]
    user: MeUser | None = None
    workspaces: list[MeWorkspace]
    active_workspace_id: str | None = None
    role: Literal["owner", "editor", "viewer"]


class QueueView(BaseModel):
    name: str
    depth: int
    oldest_age_s: float | None
    worker_healthy: bool


class QueuesOut(BaseModel):
    queues: list[QueueView]


class ClientQuota(BaseModel):
    client_id: str
    units: int
    search_calls: int


class YouTubeQuota(BaseModel):
    pt_day: date
    units_used: int
    units_limit: int
    search_calls_used: int
    search_calls_limit: int
    reset_at: AwareDatetime
    by_client: list[ClientQuota] = []


class ModelUsage(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    usd: float


class LLMUsage(BaseModel):
    usd_today: float
    by_model: list[ModelUsage] = []


class OpsQuotaOut(BaseModel):
    youtube: YouTubeQuota
    llm: LLMUsage
    tavily: dict[str, float]
    queues: list[QueueView] = []


class BacktestCreate(ApiModel):
    client_id: str
    frozen_at: AwareDatetime
    window_days: WindowDays = 30


class LabelIn(ApiModel):
    is_outlier: bool
    labeller: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class LabelOut(BaseModel):
    video_id: str
    client_id: str | None = None
    is_outlier: bool | None
    labeller: str | None = None
    note: str | None = None
    labelled_at: AwareDatetime | None = None


class PrecisionOut(BaseModel):
    precision: float | None
    recall: float | None
    n: int
    threshold: float


class ReviewIn(ApiModel):
    recommendation_id: str
    reviewer: str = Field(min_length=1, max_length=120)
    specific: int = Field(ge=1, le=5)
    evidenced: int = Field(ge=1, le=5)
    relevant: int = Field(ge=1, le=5)
    actionable: int = Field(ge=1, le=5)
    not_obvious: int = Field(ge=1, le=5)
    would_ship: bool
    comment: str | None = Field(default=None, max_length=2000)


# 12 owns these report shapes; the api passes them through unchanged
OwnerReport = dict[str, Any]
