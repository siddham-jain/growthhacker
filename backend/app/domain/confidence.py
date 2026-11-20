from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.common import DomainModel

HIGH_MIN_OUTLIERS = 5
HIGH_MIN_CHANNELS = 3


class ConfidenceLabel(StrEnum):
    high = "High"
    medium = "Medium"
    low = "Low"


class ConfidenceBasis(StrEnum):
    standard = "standard"
    first_mover = "first_mover"
    rule_based = "rule_based"


class ConfidenceRuleId(StrEnum):
    default_v1 = "default_v1"
    creator_v1 = "creator_v1"
    news_v1 = "news_v1"
    refresh_v1 = "refresh_v1"


class Confidence(DomainModel):
    label: ConfidenceLabel
    outliers: int = Field(ge=0)
    channels: int = Field(ge=0)
    basis: ConfidenceBasis = ConfidenceBasis.standard
    matching_videos: int = Field(default=0, ge=0)
    rule_id: ConfidenceRuleId = ConfidenceRuleId.default_v1
    caps_applied: list[str] = []

    @model_validator(mode="after")
    def _label_is_honest(self) -> "Confidence":
        if self.outliers > 0 and self.channels > self.outliers:
            raise ValueError("outliers cannot span more channels than there are outliers")
        if self.label is ConfidenceLabel.high and (
            self.outliers < HIGH_MIN_OUTLIERS or self.channels < HIGH_MIN_CHANNELS
        ):
            raise ValueError(f"High needs {HIGH_MIN_OUTLIERS}+ outliers across {HIGH_MIN_CHANNELS}+ channels")
        if self.basis is ConfidenceBasis.first_mover and self.label is not ConfidenceLabel.low:
            raise ValueError("first-mover signals are always Low")
        return self
