from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


ChannelId = Annotated[str, StringConstraints(pattern=r"^UC[0-9A-Za-z_-]{22}$")]
VideoId = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_-]{11}$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
Ratio = Annotated[float, Field(ge=0.0)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
Priority = Literal[1, 2, 3]
WindowDays = Literal[7, 30, 90]


class FormatSegment(StrEnum):
    short_form = "short_form"
    long_form = "long_form"
    live = "live"


class VideoFormat(StrEnum):
    short_form = "short_form"
    long_form = "long_form"
    live = "live"
    upcoming = "upcoming"
    unknown = "unknown"


class ChannelRole(StrEnum):
    own = "own"
    competitor = "competitor"
    niche = "niche"
    benchmark = "benchmark"
    creator = "creator"


class DirectnessLevel(StrEnum):
    direct = "direct"
    partial = "partial"
    mention = "mention"
    none = "none"


class SubscriberBand(StrEnum):
    lt_10k = "lt_10k"
    from_10k_to_100k = "10k_100k"
    from_100k_to_1m = "100k_1m"
    gt_1m = "gt_1m"
    hidden = "hidden"


def subscriber_band(subscriber_count: int | None) -> SubscriberBand:
    if subscriber_count is None:
        return SubscriberBand.hidden
    if subscriber_count < 10_000:
        return SubscriberBand.lt_10k
    if subscriber_count < 100_000:
        return SubscriberBand.from_10k_to_100k
    if subscriber_count < 1_000_000:
        return SubscriberBand.from_100k_to_1m
    return SubscriberBand.gt_1m


def format_key(segment: FormatSegment, length_band: str) -> str:
    return f"{segment.value}|{length_band}"
