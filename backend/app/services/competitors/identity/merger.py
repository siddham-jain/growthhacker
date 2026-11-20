from uuid import UUID

from app.domain.competitors import CandidateRole, CandidateStatus, GeoRef
from app.services.competitors.identity.normalize import normalize_name, registrable_domain, token_set_ratio
from app.services.competitors.model import CandidateEntity, KnownCandidate, LeadRef, RawCandidate

# the display name comes from the most trustworthy source that named the entity
NAME_PRIORITY = {
    "user": 0,
    "website_hint": 1,
    "web_search": 2,
    "rediscovery_news": 3,
    "llm": 4,
    "niche_search": 5,
}


def _countries(geography: list[GeoRef]) -> set[str]:
    return {geo.country_iso2 for geo in geography}


# previously tracked or suggested entities are re-checked so their youtube evidence stays fresh
CARRIED_STATUSES = frozenset(
    {
        CandidateStatus.confirmed,
        CandidateStatus.auto_confirmed,
        CandidateStatus.suggested,
        CandidateStatus.user_added,
    }
)


def attach_known(entity: CandidateEntity, known: KnownCandidate) -> None:
    entity.aliases.update(alias for alias in known.aliases if alias != entity.brand_name)
    claimed = {ref.value for ref in entity.lead_refs}
    entity.lead_refs.extend(
        LeadRef(kind="channel_id", value=channel_id, method="resolution_cache")
        for channel_id in known.channel_ids
        if channel_id not in claimed
    )
    if entity.official_domain is None and known.official_domain:
        entity.official_domain = known.official_domain


def carry_over(known: KnownCandidate, index: int) -> CandidateEntity | None:
    if known.status not in CARRIED_STATUSES:
        return None
    entity = CandidateEntity(
        key=f"k{index + 1}",
        brand_name=known.brand_name,
        normalized_name=known.normalized_name,
        official_domain=known.official_domain,
        relation=known.relation,
        owner_kind=known.owner_kind,
        known=known,
    )
    entity.flags.add("carried_over")
    attach_known(entity, known)
    return entity


def geography_compatible(left: list[GeoRef], right: list[GeoRef]) -> bool:
    left_countries, right_countries = _countries(left), _countries(right)
    return not left_countries or not right_countries or bool(left_countries & right_countries)


class EntityMerger:
    """dedups raw candidates into entities: same domain, then same channel, then fuzzy name."""

    def __init__(self, name_ratio: float) -> None:
        self.name_ratio = name_ratio

    def merge(self, raws: list[RawCandidate], known: list[KnownCandidate]) -> list[CandidateEntity]:
        entities: list[CandidateEntity] = []
        for raw in raws:
            entity = self._find(entities, raw)
            if entity is None:
                entity = CandidateEntity(
                    key=f"e{len(entities) + 1}",
                    brand_name=raw.name.strip(),
                    normalized_name=normalize_name(raw.name),
                )
                entities.append(entity)
            self._absorb(entity, raw)
        matched: set[UUID] = set()
        for entity in entities:
            entity.known = self._match_known(entity, known)
            if entity.known is not None:
                matched.add(entity.known.id)
                attach_known(entity, entity.known)
        carried = [
            carry_over(candidate, index)
            for index, candidate in enumerate(known)
            if candidate.id not in matched
        ]
        return [*entities, *(entity for entity in carried if entity is not None)]

    def merge_by_channel(self, entities: list[CandidateEntity]) -> list[CandidateEntity]:
        # second pass after resolution: two entities that resolved to one channel are one brand
        owners: dict[str, CandidateEntity] = {}
        survivors: list[CandidateEntity] = []
        for entity in entities:
            primary = entity.best_lead()
            channel_id = primary.channel_id if primary is not None and primary.score >= 0.5 else None
            target = owners.get(channel_id) if channel_id else None
            if target is None or entity.is_excluded or target.is_excluded:
                if channel_id:
                    owners.setdefault(channel_id, entity)
                survivors.append(entity)
                continue
            self._fold(target, entity)
        return survivors

    def _find(self, entities: list[CandidateEntity], raw: RawCandidate) -> CandidateEntity | None:
        domain = registrable_domain(raw.domain)
        name = normalize_name(raw.name)
        for entity in entities:
            if domain and entity.official_domain == domain:
                return entity
        for entity in entities:
            if raw.channel_id and raw.channel_id in entity.channel_hints:
                return entity
        for entity in entities:
            if domain and entity.official_domain and entity.official_domain != domain:
                continue
            similar = any(
                token_set_ratio(name, normalize_name(other)) >= self.name_ratio for other in entity.names
            )
            if similar and geography_compatible(entity.geography, raw.geography):
                return entity
        return None

    def _absorb(self, entity: CandidateEntity, raw: RawCandidate) -> None:
        kinds_before = entity.source_kinds
        entity.sources.append(raw.source)
        if self._takes_name(raw.source.kind, kinds_before):
            if entity.brand_name != raw.name.strip():
                entity.aliases.add(entity.brand_name)
            entity.brand_name = raw.name.strip()
            entity.normalized_name = normalize_name(raw.name)
        elif raw.name.strip() != entity.brand_name:
            entity.aliases.add(raw.name.strip())
        entity.aliases.update(alias.strip() for alias in raw.aliases if alias.strip())
        entity.aliases.discard(entity.brand_name)
        self._absorb_domain(entity, raw)
        entity.geography.extend(geo for geo in raw.geography if geo not in entity.geography)
        if raw.relation_guess != "unknown":
            entity.relation_guesses.append(raw.relation_guess)
        if raw.channel_id:
            entity.channel_hints.setdefault(raw.channel_id, raw.channel_record)
        entity.lead_refs.extend(raw.lead_refs)
        entity.query_hits = max(entity.query_hits, raw.query_hits)
        entity.total_hits = max(entity.total_hits, raw.total_hits)
        if raw.intended_role is not None:
            entity.intended_role = raw.intended_role
        if raw.source.kind == "llm" and entity.source_kinds == {"llm"}:
            entity.flags.add("llm_only")
        elif len(entity.source_kinds) > 1:
            entity.flags.discard("llm_only")

    @staticmethod
    def _takes_name(kind: str, kinds_before: set[str]) -> bool:
        if not kinds_before:
            return True
        best_before = min(NAME_PRIORITY.get(existing, 9) for existing in kinds_before)
        return NAME_PRIORITY.get(kind, 9) < best_before

    @staticmethod
    def _absorb_domain(entity: CandidateEntity, raw: RawCandidate) -> None:
        domain = registrable_domain(raw.domain)
        if not domain:
            return
        if raw.source.kind == "llm":
            if entity.official_domain is None:
                entity.official_domain = domain
                entity.flags.add("domain_unverified")
            return
        if entity.official_domain is None or "domain_unverified" in entity.flags:
            entity.official_domain = domain
            entity.flags.discard("domain_unverified")

    def _fold(self, target: CandidateEntity, other: CandidateEntity) -> None:
        target.sources.extend(other.sources)
        target.aliases.update({other.brand_name, *other.aliases} - {target.brand_name})
        target.geography.extend(geo for geo in other.geography if geo not in target.geography)
        target.relation_guesses.extend(other.relation_guesses)
        target.query_hits = max(target.query_hits, other.query_hits)
        target.total_hits = max(target.total_hits, other.total_hits)
        target.flags.update(other.flags - {"llm_only", "domain_unverified"})
        if len(target.source_kinds) > 1:
            target.flags.discard("llm_only")
        for channel_id, lead in other.leads.items():
            target.leads.setdefault(channel_id, lead)
        if target.intended_role is None:
            target.intended_role = other.intended_role
        if target.official_domain is None:
            target.official_domain = other.official_domain

    def _match_known(self, entity: CandidateEntity, known: list[KnownCandidate]) -> KnownCandidate | None:
        for candidate in known:
            if entity.official_domain and candidate.official_domain == entity.official_domain:
                return candidate
        for candidate in known:
            if candidate.normalized_name == entity.normalized_name:
                return candidate
            if set(candidate.channel_ids) & set(entity.channel_hints):
                return candidate
        for candidate in known:
            if token_set_ratio(candidate.normalized_name, entity.normalized_name) >= self.name_ratio:
                return candidate
        return None


def suppress_rejected(entities: list[CandidateEntity]) -> None:
    for entity in entities:
        known = entity.known
        if known is not None and known.status is CandidateStatus.rejected and known.decided_by == "user":
            entity.status = CandidateStatus.rejected
            entity.rejection_reason = known.rejection_reason or "rejected earlier"
            entity.flags.add("previously_rejected")
        elif known is not None and known.decided_by == "user" and known.role is not CandidateRole.excluded:
            entity.intended_role = entity.intended_role or known.role
