from dataclasses import dataclass, field

from app.domain.competitors import BusinessRelevance, CandidateRole, CompetitorRelation, OwnerKind
from app.domain.youtube import OwnershipLabel
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.score.confidence import Confidence
from app.services.competitors.score.youtube import YouTubeAssessment

TIER_RANK = {"high": 0, "medium": 1, "low": 2}
LABEL_RANK = {OwnershipLabel.verified: 0, OwnershipLabel.likely: 1, OwnershipLabel.uncertain: 2, None: 3}
OWNED = frozenset({OwnershipLabel.verified, OwnershipLabel.likely})


def default_role(entity: CandidateEntity) -> CandidateRole:
    relation = entity.relation
    if entity.is_excluded or relation is None:
        return CandidateRole.excluded
    if relation in {CompetitorRelation.direct, CompetitorRelation.direct_public}:
        return CandidateRole.competitor_set
    if relation is CompetitorRelation.benchmark:
        return CandidateRole.benchmark_set
    if relation is CompetitorRelation.content and entity.owner_kind is OwnerKind.individual:
        return CandidateRole.creator_seed
    if relation in {CompetitorRelation.content, CompetitorRelation.aggregator}:
        return CandidateRole.niche_set
    return CandidateRole.excluded


@dataclass
class AssessedCandidate:
    entity: CandidateEntity
    business: BusinessRelevance
    assessments: dict[str, YouTubeAssessment] = field(default_factory=dict)
    primary: ChannelLead | None = None
    confidence: Confidence = "low"
    role: CandidateRole = CandidateRole.excluded

    @property
    def primary_assessment(self) -> YouTubeAssessment | None:
        return self.assessments.get(self.primary.channel_id) if self.primary is not None else None

    @property
    def channel_id(self) -> str | None:
        return self.primary.channel_id if self.primary is not None else None

    @property
    def owned(self) -> bool:
        return self.primary is not None and self.primary.label in OWNED

    @property
    def active(self) -> bool:
        assessment = self.primary_assessment
        return assessment is not None and assessment.evidence.uploads_90d > 0

    @property
    def youtube_tier(self) -> str:
        assessment = self.primary_assessment
        return assessment.tier if assessment is not None else "low"

    @property
    def matching_uploads(self) -> int:
        assessment = self.primary_assessment
        return assessment.matching_uploads if assessment is not None else 0

    @property
    def size_band(self) -> str:
        assessment = self.primary_assessment
        return assessment.relevance.size_band if assessment is not None else "unknown"

    def rank_key(self) -> tuple:
        # lexicographic: web tier, then youtube tier, then one raw count; no blended score exists
        return (
            TIER_RANK[self.business.tier],
            TIER_RANK[self.youtube_tier],
            -self.matching_uploads,
            LABEL_RANK.get(self.primary.label if self.primary else None, 3),
            self.entity.normalized_name,
        )

    def youtube_key(self) -> tuple:
        return (TIER_RANK[self.youtube_tier], -self.matching_uploads, self.entity.normalized_name)
