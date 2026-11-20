from app.core.config import CompetitorSettings
from app.domain.youtube import OwnershipLabel
from app.services.competitors.model import CompetitorSetProposal
from app.services.competitors.select.assessment import AssessedCandidate

BLOCKING_FLAGS = frozenset({"llm_only", "uncorroborated", "relation_unclassified", "public_institution"})


class AutoConfirmPolicy:
    """a demo never runs on a guessed set: three strongly owned, corroborated competitors are required."""

    def __init__(self, settings: CompetitorSettings) -> None:
        self.settings = settings

    def allows(
        self, proposal: CompetitorSetProposal, assessed: list[AssessedCandidate], degraded: set[str]
    ) -> bool:
        if "llm" in degraded or not proposal.ready:
            return False
        by_channel = {item.channel_id: item for item in assessed if item.channel_id}
        members = [
            by_channel[channel_id]
            for channel_id in proposal.competitor_channel_ids
            if channel_id in by_channel
        ]
        if any(BLOCKING_FLAGS & item.entity.flags for item in members):
            return False
        strong = [
            item
            for item in members
            if item.primary.label is OwnershipLabel.verified
            or (
                item.primary.label is OwnershipLabel.likely
                and item.primary.score >= self.settings.auto_confirm_min_likely_score
            )
        ]
        return len(strong) >= self.settings.min_competitors
