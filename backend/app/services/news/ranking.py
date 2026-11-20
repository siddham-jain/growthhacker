from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.domain.news import (
    ConfirmationStatus,
    CredibilityAssessment,
    DemandCheck,
    EventType,
    NewsJudgement,
    RelevanceBand,
)
from app.domain.profile import WatchEntity
from app.services.news.text import contains_phrase
from app.services.news.tiers import TIER_RANK
from app.services.news.versions import strip_versions

Urgency = Literal["now", "this_week", "evergreen_window"]
BAND_WEIGHT = {
    RelevanceBand.none: 0.0,
    RelevanceBand.tangential: 0.2,
    RelevanceBand.adjacent: 0.45,
    RelevanceBand.strong: 0.75,
    RelevanceBand.direct: 0.95,
}
BAND_RANK = {band: index for index, band in enumerate(RelevanceBand)}
# saturation preference for ordering: early demand beats no videos, crowded topics come last
SATURATION_RANK = {"emerging": 3, "active": 2, "empty": 1, "crowded": 0}
LONG_LIVED_EVENTS = frozenset({EventType.clinical_guideline, EventType.regulatory_approval})


@dataclass(frozen=True)
class Decay:
    half_life_hours: int
    ttl_hours: int


DECAY_BY_EVENT: dict[EventType, Decay] = {
    EventType.model_release: Decay(48, 7 * 24),
    EventType.product_launch: Decay(48, 7 * 24),
    EventType.pricing_change: Decay(36, 5 * 24),
    EventType.partnership: Decay(36, 5 * 24),
    EventType.funding_or_ipo: Decay(36, 5 * 24),
    EventType.incident: Decay(36, 5 * 24),
    EventType.regulatory_approval: Decay(7 * 24, 21 * 24),
    EventType.recall: Decay(7 * 24, 21 * 24),
    EventType.regulatory_action: Decay(7 * 24, 21 * 24),
    EventType.clinical_guideline: Decay(14 * 24, 45 * 24),
    EventType.policy_change: Decay(14 * 24, 45 * 24),
    EventType.research_publication: Decay(7 * 24, 21 * 24),
    EventType.public_health_advisory: Decay(5 * 24, 30 * 24),
    EventType.outbreak: Decay(5 * 24, 30 * 24),
    EventType.other: Decay(48, 7 * 24),
}
UPCOMING_GRACE = timedelta(days=3)


def decay_for(event_type: EventType, overrides: Mapping[EventType, int] | None = None) -> Decay:
    base = DECAY_BY_EVENT[event_type]
    if overrides and event_type in overrides:
        return Decay(overrides[event_type], base.ttl_hours)
    return base


def relevance_score(
    judgement: NewsJudgement, watch: Sequence[WatchEntity], *, primary_region_weight: float = 1.0
) -> float:
    key = judgement.key_entity
    watched = any(
        contains_phrase(key.text, name) or contains_phrase(key.canonical, name)
        for entity in watch
        for name in (entity.name, *entity.aliases)
    )
    score = BAND_WEIGHT[judgement.relevance_band]
    score += (
        0.05 * watched
        + 0.05 * min(len(judgement.offering_links), 2)
        + 0.05 * bool(judgement.buyer_question_ids)
    )
    score = min(score, 1.0)
    if judgement.region_relevance == "other_region_only":
        score *= 0.3
    elif judgement.region_relevance == "unclear":
        score *= 0.8
    return round(score * primary_region_weight, 4)


def freshness(first_published_at: datetime, now: datetime, half_life_hours: int) -> float:
    age_hours = max((now - first_published_at).total_seconds() / 3600, 0.0)
    return 0.5 ** (age_hours / half_life_hours)


def expires_at(judgement: NewsJudgement, first_published_at: datetime, decay: Decay) -> datetime:
    if judgement.confirmation is ConfirmationStatus.officially_announced_upcoming and judgement.event_date:
        event_at = datetime.combine(
            judgement.event_date, datetime.min.time(), tzinfo=first_published_at.tzinfo
        )
        return max(event_at + UPCOMING_GRACE, first_published_at + timedelta(hours=decay.ttl_hours))
    return first_published_at + timedelta(hours=decay.ttl_hours)


def urgency(fresh: float, demand: DemandCheck, event_type: EventType) -> Urgency:
    if fresh >= 0.6 and demand.saturation in {"empty", "emerging"} and event_type not in LONG_LIVED_EVENTS:
        return "now"
    if fresh >= 0.3:
        return "this_week"
    return "evergreen_window"


@dataclass(frozen=True)
class RankedCandidate:
    key: str
    family: str
    story_id: str
    band: RelevanceBand
    credibility: CredibilityAssessment
    freshness: float
    saturation: str


def order_key(candidate: RankedCandidate) -> tuple:
    # lexicographic: relevance band, credibility tier, freshness, saturation; never one blended number
    return (
        BAND_RANK[candidate.band],
        TIER_RANK[candidate.credibility.best_tier],
        candidate.credibility.independent_outlets,
        round(candidate.freshness, 3),
        SATURATION_RANK.get(candidate.saturation, 0),
        candidate.key,
    )


def entity_family(canonical: str) -> str:
    return strip_versions(canonical)


def flood_control[T](
    candidates: Sequence[T], rank: Callable[[T], RankedCandidate], cap: int, per_family: int = 2
) -> tuple[list[T], list[T]]:
    ordered = sorted(candidates, key=lambda c: order_key(rank(c)), reverse=True)
    kept: list[T] = []
    capped: list[T] = []
    families: dict[str, int] = {}
    stories: set[str] = set()
    for candidate in ordered:
        ranked = rank(candidate)
        if len(kept) >= cap or families.get(ranked.family, 0) >= per_family or ranked.story_id in stories:
            capped.append(candidate)
            continue
        kept.append(candidate)
        families[ranked.family] = families.get(ranked.family, 0) + 1
        stories.add(ranked.story_id)
    return kept, capped
