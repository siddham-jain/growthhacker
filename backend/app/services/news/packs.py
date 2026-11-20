from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from app.core.registry import Registry
from app.domain.news import EventType, SourceTier

PACKS_DIR = Path(__file__).resolve().parent / "packs"
TIERS_FILE = "_tiers.yaml"


class PackModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeedSpec(PackModel):
    name: str
    url: str
    tier: SourceTier = SourceTier.tier3_low


class DomainGroup(PackModel):
    name: str
    domains: list[str]
    query: str


class SourcePack(PackModel):
    key: str
    description: str = ""
    industries: list[str] = []
    countries: list[str] = []
    event_terms: dict[str, list[str]] = {}
    feeds: list[FeedSpec] = []
    domain_groups: list[DomainGroup] = []
    primary_domains: list[str] = []
    tier_overrides: dict[str, SourceTier] = {}
    pubmed: bool = False
    arxiv_categories: list[str] = []
    hf_orgs: list[str] = []
    who_outbreaks: bool = False
    openfda_approvals: bool = False
    half_life_hours: dict[EventType, int] = {}
    alias_pairs: list[tuple[str, str]] = []
    ambiguous_terms: list[str] = []
    alarm_words: list[str] = []
    demo_event_types: list[EventType] = []
    paywalled_domains: list[str] = []


class GlobalTiers(PackModel):
    tiers: dict[SourceTier, list[str]]
    paywalled: list[str] = []
    press_release: list[str] = []
    social: list[str] = []
    preprint: list[str] = []
    excluded: list[str] = []


class SourcePackRegistry:
    def __init__(self, packs: Registry[SourcePack], tiers: GlobalTiers) -> None:
        self._packs = packs
        self.tiers = tiers

    def get(self, key: str) -> SourcePack:
        return self._packs.get(key)

    def resolve(self, keys: list[str]) -> list[SourcePack]:
        return [self._packs.get(key) for key in keys if key in self._packs]

    def for_profile(self, keys: list[str], industry: str | None, country: str | None) -> list[SourcePack]:
        """the profile's own packs, else every pack declared for its industry (and country, when one is set)."""
        if keys:
            return self.resolve(keys)
        return [
            pack
            for pack in (self._packs.get(key) for key in sorted(self._packs.keys()))
            if industry in pack.industries and (not pack.countries or country in pack.countries)
        ]

    def keys(self) -> list[str]:
        return self._packs.keys()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_pack_registry(directory: Path = PACKS_DIR) -> SourcePackRegistry:
    packs: Registry[SourcePack] = Registry("source_pack_registry")
    for path in sorted(directory.glob("*.yaml")):
        if path.name == TIERS_FILE:
            continue
        pack = SourcePack.model_validate(_load(path))
        packs.add(pack.key, pack)
    return SourcePackRegistry(packs, GlobalTiers.model_validate(_load(directory / TIERS_FILE)))


@lru_cache(maxsize=1)
def default_pack_registry() -> SourcePackRegistry:
    return load_pack_registry()
