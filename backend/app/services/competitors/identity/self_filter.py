from app.domain.competitors import CandidateStatus, CompetitorRelation
from app.domain.profile import BusinessProfile
from app.services.competitors.identity.normalize import (
    distinctive_tokens,
    normalize_name,
    registrable_domain,
    token_set_ratio,
)
from app.services.competitors.model import CandidateEntity

RELATED_RELATION = {
    "parent": CompetitorRelation.parent,
    "sister": CompetitorRelation.subsidiary,
    "subsidiary": CompetitorRelation.subsidiary,
    "unknown": CompetitorRelation.subsidiary,
}
GROUP_GUESSES = frozenset({"subsidiary", "sister", "parent"})


class SelfIdentityFilter:
    """marks the client, its group brands and its owner so they never become competitors."""

    def __init__(
        self, profile: BusinessProfile, own_channel_ids: list[str], client_domains: set[str]
    ) -> None:
        identity = profile.identity
        self.brand_names = {normalize_name(name) for name in [identity.name, *identity.brand_aliases] if name}
        self.brand_names.update(
            normalize_name(term.term) for term in profile.brand_terms if term.kind == "company"
        )
        self.brand_tokens = set().union(*(distinctive_tokens(name) for name in self.brand_names))
        self.own_channel_ids = set(own_channel_ids)
        self.client_domains = client_domains
        self.related = [
            (normalize_name(brand.name), registrable_domain(brand.domain), RELATED_RELATION[brand.relation])
            for brand in identity.related_brands
        ]

    def apply(self, entities: list[CandidateEntity]) -> list[CandidateEntity]:
        group_members = []
        for entity in entities:
            relation = self.classify(entity)
            if relation is None:
                continue
            entity.relation = relation
            entity.status = CandidateStatus.excluded
            entity.rejection_reason = relation.value
            entity.flags.add("own_group" if relation is not CompetitorRelation.self_ else "self")
            group_members.append(entity)
        return group_members

    def classify(self, entity: CandidateEntity) -> CompetitorRelation | None:
        if entity.official_domain and entity.official_domain in self.client_domains:
            return CompetitorRelation.self_
        if self.own_channel_ids & set(entity.channel_hints):
            return CompetitorRelation.self_
        names = {normalize_name(name) for name in entity.names}
        if names & self.brand_names:
            return CompetitorRelation.self_
        for related_name, related_domain, relation in self.related:
            if related_domain and entity.official_domain == related_domain:
                return relation
            if any(token_set_ratio(name, related_name) >= 92 for name in names):
                return relation
        shares_brand_token = any(distinctive_tokens(name) & self.brand_tokens for name in names)
        if shares_brand_token and GROUP_GUESSES & set(entity.relation_guesses):
            return (
                CompetitorRelation.parent
                if "parent" in entity.relation_guesses
                else CompetitorRelation.subsidiary
            )
        return None
