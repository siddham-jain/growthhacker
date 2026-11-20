from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.domain.runs import RunNotice

WindowDays = Literal[7, 30, 90]


def _valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown IANA timezone '{value}'") from None
    return value


CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
LanguageTag = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]
Timezone = Annotated[str, AfterValidator(_valid_timezone)]


class ApiModel(BaseModel):
    # request bodies reject unknown fields so typos are not silently ignored
    model_config = ConfigDict(extra="forbid")


class Market(ApiModel):
    country: CountryCode
    language: LanguageTag
    timezone: Timezone


class Freshness(BaseModel):
    data_through: AwareDatetime
    age_hours: float
    stale: bool
    stale_reasons: list[str] = []


class ResultEnvelope[T](BaseModel):
    """every analytics and recommendation read: the numbers plus where and when they came from."""

    data: T
    window_days: int
    as_of: AwareDatetime
    run_id: str
    generated_at: AwareDatetime
    freshness: Freshness
    notices: list[RunNotice] = []
    prev_run_id: str | None = None


class StatusOut(BaseModel):
    status: str


class CountOut(BaseModel):
    updated: int = Field(ge=0)
