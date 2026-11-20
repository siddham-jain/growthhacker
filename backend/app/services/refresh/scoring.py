from collections.abc import Mapping

from app.core.config import RefreshPriorityWeights, RefreshSettings
from app.domain.confidence import Confidence, ConfidenceBasis, ConfidenceLabel, ConfidenceRuleId
from app.services.refresh.demand import DemandEvidence
from app.services.refresh.eligibility import VelocityBasis
from app.services.refresh.triggers.base import Finding

HIGH_RELEVANCE = 0.75
MEDIUM_MIN_OUTLIERS = 3
MEDIUM_MIN_CHANNELS = 2


def percentile_ranks(velocities: Mapping[str, float]) -> dict[str, float]:
    # share of gated videos at or below this one's velocity, within the client's own channel only
    values = sorted(velocities.values())
    total = len(values)
    return {video_id: sum(v <= value for v in values) / total for video_id, value in velocities.items()}


class RefreshScorer:
    """internal ordering only; never displayed. news urgency breaks ties instead of entering the score."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def priority(self, finding: Finding, velocity_rank: float) -> float:
        if finding.keyword_trigger:
            return self._weighted(self.settings.keyword_weights, velocity_rank, finding)
        return self._weighted(self.settings.hygiene_weights, velocity_rank, finding)

    @staticmethod
    def _weighted(weights: RefreshPriorityWeights, rank: float, finding: Finding) -> float:
        total = weights.velocity + weights.relevance + weights.demand
        value = (
            weights.velocity * rank + weights.relevance * finding.strength + weights.demand * finding.demand
        )
        return round(100 * value / total, 2)

    def shown(self, finding: Finding, priority: float) -> bool:
        floor = self.settings.keyword_show_min if finding.keyword_trigger else self.settings.hygiene_show_min
        return priority >= floor

    @staticmethod
    def sort_key(finding: Finding, priority: float) -> tuple[float, float]:
        return priority, finding.urgency


def rule_based_confidence() -> Confidence:
    # a verified defect needs no outlier evidence; the label is honest about the basis, not the count
    return Confidence(
        label=ConfidenceLabel.medium,
        outliers=0,
        channels=0,
        basis=ConfidenceBasis.rule_based,
        rule_id=ConfidenceRuleId.refresh_v1,
    )


def keyword_confidence(demand: DemandEvidence, strength: float, basis: VelocityBasis) -> Confidence:
    outliers, channels = len(demand.outlier_refs), demand.outlier_channels
    caps: list[str] = []
    if outliers >= 5 and channels >= 3:
        if strength < HIGH_RELEVANCE:
            caps.append("relevance_below_high")
        if basis != "snapshots":
            caps.append("provisional_velocity")
    if outliers >= 5 and channels >= 3 and not caps:
        label = ConfidenceLabel.high
    elif outliers >= MEDIUM_MIN_OUTLIERS and channels >= MEDIUM_MIN_CHANNELS:
        label = ConfidenceLabel.medium
    else:
        label = ConfidenceLabel.low
    return Confidence(
        label=label,
        outliers=outliers,
        channels=channels,
        basis=ConfidenceBasis.standard,
        matching_videos=len(demand.refs),
        rule_id=ConfidenceRuleId.refresh_v1,
        caps_applied=caps,
    )
