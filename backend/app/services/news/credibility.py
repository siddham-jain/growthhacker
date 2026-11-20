from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.news import (
    ConfirmationStatus,
    CredibilityAssessment,
    NewsItem,
    NewsJudgement,
    SourceTier,
)
from app.services.news.context import NewsClientContext
from app.services.news.tiers import TIER1, best_tier, host_matches

TIER_WEIGHT = {
    SourceTier.tier1_primary: 1.0,
    SourceTier.tier1_authority: 1.0,
    SourceTier.tier2_established: 0.8,
    SourceTier.tier3_low: 0.4,
    SourceTier.blocked: 0.0,
}
UNCONFIRMED = frozenset({ConfirmationStatus.rumour_or_leak, ConfirmationStatus.speculative})
OUTLET_BONUS = 0.05
MAX_OUTLET_BONUS_STEPS = 4
RESEARCH_SOURCES = frozenset({"pubmed", "arxiv"})


def item_tier(item: NewsItem, ctx: NewsClientContext) -> SourceTier:
    # the client's and competitors' own sites are primary sources for news about them
    own = [*ctx.own_domains, *(c.official_domain for c in ctx.confirmed_competitors if c.official_domain)]
    if item.source_tier is not SourceTier.blocked and own and host_matches(item.domain, own):
        return SourceTier.tier1_primary
    return item.source_tier


def is_peer_reviewed(item: NewsItem) -> bool:
    return item.source_key == "pubmed" and "preprint" not in item.flags


@dataclass(frozen=True)
class CredibilityVerdict:
    passed: bool
    reason: str | None


class CredibilityScorer:
    def __init__(self, minimum: float) -> None:
        self._minimum = minimum

    def assess(
        self, items: Sequence[NewsItem], judgement: NewsJudgement, ctx: NewsClientContext
    ) -> CredibilityAssessment:
        supporting = [item for item in items if "social" not in item.flags]
        tiers = [item_tier(item, ctx) for item in supporting]
        top = best_tier(tiers) if tiers else SourceTier.tier3_low
        outlets = len({item.domain for item in supporting})
        research = [
            item for item in supporting if item.source_key in RESEARCH_SOURCES or "preprint" in item.flags
        ]
        peer_reviewed = any(is_peer_reviewed(item) for item in research) if research else None
        authority = SourceTier.tier1_authority in tiers or bool(peer_reviewed)
        reasons = [f"BEST_TIER_{top.value.upper()}", f"OUTLETS_{outlets}"]
        score = TIER_WEIGHT[top] + OUTLET_BONUS * min(max(outlets - 1, 0), MAX_OUTLET_BONUS_STEPS)
        score = min(score, 1.0)
        if judgement.confirmation in UNCONFIRMED:
            score *= 0.5
            reasons.append("UNCONFIRMED")
        if judgement.conflicting_reports:
            score *= 0.7
            reasons.append("CONFLICTING_REPORTS")
        if supporting and all("press_release" in item.flags for item in supporting):
            score = min(score, 0.5)
            reasons.append("PRESS_RELEASE_ONLY")
        if supporting and all("preprint" in item.flags for item in supporting):
            reasons.append("PREPRINT_ONLY")
        if not supporting:
            score = 0.0
            reasons.append("SOCIAL_ONLY")
        return CredibilityAssessment(
            best_tier=top,
            independent_outlets=outlets,
            has_primary_source=SourceTier.tier1_primary in tiers,
            has_authoritative_health_source=authority,
            peer_reviewed=peer_reviewed,
            score=round(score, 4),
            reasons=reasons,
        )

    def verdict(
        self, assessment: CredibilityAssessment, judgement: NewsJudgement, ctx: NewsClientContext
    ) -> CredibilityVerdict:
        if assessment.score < self._minimum:
            return CredibilityVerdict(False, "low_credibility")
        if ctx.is_health:
            if not assessment.has_authoritative_health_source:
                return CredibilityVerdict(False, "health_needs_authority")
            if judgement.confirmation in UNCONFIRMED:
                return CredibilityVerdict(False, "health_unconfirmed")
            if "PREPRINT_ONLY" in assessment.reasons:
                return CredibilityVerdict(False, "health_preprint_only")
        return CredibilityVerdict(True, None)


def corroborated(assessment: CredibilityAssessment, ctx: NewsClientContext, min_sources: int) -> bool:
    """≥1 tier-1 source or ≥2 independent tier-2 outlets, and at least min_sources independent outlets."""
    if ctx.is_health and not assessment.has_authoritative_health_source:
        return False
    tier_ok = assessment.best_tier in TIER1 or (
        assessment.best_tier is SourceTier.tier2_established and assessment.independent_outlets >= 2
    )
    return tier_ok and assessment.independent_outlets >= min_sources
