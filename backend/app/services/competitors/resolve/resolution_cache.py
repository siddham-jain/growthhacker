from datetime import datetime
from typing import Literal, Protocol

from app.domain.common import DomainModel
from app.domain.youtube import OwnershipLabel, OwnershipSignal
from app.services.competitors.model import CandidateEntity

KeyType = Literal["domain", "name_country"]


class CachedResolution(DomainModel):
    # public brand-to-channel facts only; no client data ever enters this cache
    key_type: KeyType
    key: str
    channel_id: str
    label: OwnershipLabel
    score: float
    method: Literal["oauth", "link", "user"] | None = None
    signals: list[OwnershipSignal] = []
    verified_at: datetime


class BrandChannelCache(Protocol):
    async def lookup(self, keys: list[tuple[KeyType, str]], now: datetime) -> list[CachedResolution]: ...

    async def store(self, entries: list[CachedResolution], ttl_days: int) -> None: ...


def cache_keys(entity: CandidateEntity, market: str | None) -> list[tuple[KeyType, str]]:
    keys: list[tuple[KeyType, str]] = []
    if entity.official_domain and "domain_unverified" not in entity.flags:
        keys.append(("domain", entity.official_domain))
    countries = sorted({geo.country_iso2 for geo in entity.geography}) or ([market] if market else [])
    keys.extend(("name_country", f"{entity.normalized_name}|{country}") for country in countries)
    return keys
