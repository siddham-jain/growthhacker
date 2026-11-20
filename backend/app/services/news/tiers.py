from collections.abc import Iterable, Mapping

from app.domain.news import SourceTier
from app.services.news.packs import GlobalTiers, SourcePack

TIER_RANK: dict[SourceTier, int] = {
    SourceTier.blocked: 0,
    SourceTier.tier3_low: 1,
    SourceTier.tier2_established: 2,
    SourceTier.tier1_authority: 3,
    SourceTier.tier1_primary: 3,
}
TIER1 = frozenset({SourceTier.tier1_primary, SourceTier.tier1_authority})


def host_matches(host: str, domains: Iterable[str]) -> bool:
    host = host.casefold()
    return any(host == domain or host.endswith("." + domain) for domain in (d.casefold() for d in domains))


def best_tier(tiers: Iterable[SourceTier]) -> SourceTier:
    return max(
        tiers,
        key=lambda tier: (TIER_RANK[tier], tier is SourceTier.tier1_primary),
        default=SourceTier.tier3_low,
    )


class SourceTierResolver:
    """domain -> tier: blocklist, curated table, pack overrides, primary domains, global lists, then tier3."""

    def __init__(
        self,
        tiers: GlobalTiers,
        packs: Iterable[SourcePack] = (),
        curated: Mapping[str, SourceTier] | None = None,
        primary_domains: Iterable[str] = (),
    ) -> None:
        self._tiers = tiers
        self._packs = list(packs)
        self._curated = dict(curated or {})
        self._primary = set(primary_domains) | {d for pack in self._packs for d in pack.primary_domains}

    def resolve(self, host: str) -> SourceTier:
        blocked = self._tiers.tiers.get(SourceTier.blocked, [])
        if host_matches(host, blocked):
            return SourceTier.blocked
        for domain, tier in self._curated.items():
            if host_matches(host, [domain]):
                return tier
        for pack in self._packs:
            for domain, tier in pack.tier_overrides.items():
                if host_matches(host, [domain]):
                    return tier
        if host_matches(host, self._primary):
            return SourceTier.tier1_primary
        for tier in (SourceTier.tier1_authority, SourceTier.tier2_established):
            if host_matches(host, self._tiers.tiers.get(tier, [])):
                return tier
        return SourceTier.tier3_low
