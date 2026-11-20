from typing import Literal

from app.domain.competitors import BusinessRelevance
from app.domain.youtube import OwnershipLabel
from app.services.competitors.model import CandidateEntity, ChannelLead

Confidence = Literal["high", "medium", "low"]


def candidate_confidence(
    entity: CandidateEntity, business: BusinessRelevance, primary: ChannelLead | None, thin_profile: bool
) -> Confidence:
    label = primary.label if primary is not None else None
    inactive = primary is not None and "inactive" in primary.flags
    if (
        label is OwnershipLabel.verified
        and business.tier == "high"
        and len(entity.source_kinds) >= 2
        and not inactive
        and not thin_profile
    ):
        return "high"
    if label in {OwnershipLabel.verified, OwnershipLabel.likely} and business.tier in {"high", "medium"}:
        return "medium"
    return "low"
