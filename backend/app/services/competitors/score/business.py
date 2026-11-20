from dataclasses import dataclass

from app.core.config import CompetitorSettings
from app.domain.competitors import BusinessRelevance, CompetitorRelation
from app.domain.profile import BusinessProfile
from app.services.competitors.identity.normalize import normalize_text
from app.services.competitors.model import CandidateEntity
from app.services.competitors.registries import business_scoring_registry

VERDICT_VALUE = {"yes": 1.0, "partial": 0.5}


@dataclass(frozen=True)
class BusinessWeights:
    offering: float
    geo: float
    agreement: float


business_scoring_registry.add("default", BusinessWeights(offering=0.5, geo=0.3, agreement=0.2))
# benchmarks are elsewhere by definition and content channels are judged on youtube, so geography is ignored
business_scoring_registry.add("content", BusinessWeights(offering=0.6, geo=0.0, agreement=0.4))
business_scoring_registry.add("benchmark", BusinessWeights(offering=0.6, geo=0.0, agreement=0.4))


def offering_weight(priority: int) -> int:
    # priority 1 is the most important offering
    return 4 - priority


class BusinessRelevanceScorer:
    """web-derived relevance only; it is bucketed into a tier and never combined with youtube data."""

    def __init__(self, profile: BusinessProfile, settings: CompetitorSettings) -> None:
        self.profile = profile
        self.settings = settings
        locations = profile.locations
        self.cities = {normalize_text(city) for city in locations.cities}
        for item in locations.items:
            self.cities.update(normalize_text(name) for name in [item.city or "", *item.aliases] if name)
        self.regions = {normalize_text(region) for region in locations.regions}
        self.regions.update(normalize_text(item.region) for item in locations.items if item.region)
        identity = profile.identity
        self.countries = set(locations.countries) | set(identity.primary_markets)
        if identity.country:
            self.countries.add(identity.country)

    def score(self, entity: CandidateEntity) -> BusinessRelevance:
        relation = entity.relation or CompetitorRelation.direct
        weights = (
            business_scoring_registry.get(relation.value)
            if relation.value in business_scoring_registry
            else business_scoring_registry.get("default")
        )
        offering_overlap = self.offering_overlap(entity)
        geo_overlap, geo_basis = self.geo_overlap(entity)
        agreement = min(1.0, entity.independent_sources / 3)
        points = (
            weights.offering * offering_overlap + weights.geo * geo_overlap + weights.agreement * agreement
        )
        if points >= self.settings.business_tier_high:
            tier = "high"
        elif points >= self.settings.business_tier_medium:
            tier = "medium"
        else:
            tier = "low"
        return BusinessRelevance(
            offering_overlap=round(offering_overlap, 4),
            matched_offerings=entity.offering_matches,
            geo_overlap=geo_overlap,
            geo_basis=geo_basis,
            source_agreement=round(agreement, 4),
            tier=tier,
        )

    def offering_overlap(self, entity: CandidateEntity) -> float:
        weights = {offering.id: offering_weight(offering.priority) for offering in self.profile.offerings}
        total = sum(weights.values())
        if not total:
            return 0.0
        matched = sum(
            weights.get(match.offering_id, 0) * VERDICT_VALUE[match.verdict]
            for match in entity.offering_matches
        )
        return min(matched / total, 1.0)

    def geo_overlap(self, entity: CandidateEntity) -> tuple[float, str]:
        if not entity.geography:
            entity.flags.add("geo_unknown")
            return 0.3, "location unknown"
        for geo in entity.geography:
            if geo.city and normalize_text(geo.city) in self.cities:
                return 1.0, f"same city: {geo.city}"
        for geo in entity.geography:
            if geo.region and normalize_text(geo.region) in self.regions:
                return 0.8, f"same region: {geo.region}"
        for geo in entity.geography:
            if geo.country_iso2 in self.countries:
                return 0.6, f"same country: {geo.country_iso2}"
        return 0.0, "no shared market"
