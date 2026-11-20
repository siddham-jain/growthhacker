from dataclasses import dataclass
from typing import Protocol

from app.ports.crawler import SiteCrawler
from app.ports.search import WebSearch
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, LeadRef
from app.services.competitors.resolve.resolution_cache import BrandChannelCache
from app.services.competitors.youtube_access import MeteredYouTube


@dataclass(frozen=True)
class ResolverDeps:
    youtube: MeteredYouTube
    web_search: WebSearch | None
    crawler: SiteCrawler | None
    cache: BrandChannelCache | None


@dataclass(frozen=True)
class ResolverCost:
    # upper bound per entity, used to order resolvers cheapest first
    yt_units: int = 0
    search_calls: int = 0
    tavily_credits: int = 0

    @property
    def rank(self) -> tuple[int, int, int]:
        return self.search_calls, self.yt_units, self.tavily_credits


class ChannelResolver(Protocol):
    name: str
    order: int
    cost: ResolverCost

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool: ...

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]: ...


def has_likely_lead(entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
    threshold = ctx.settings.ownership.likely_min
    return any(lead.score >= threshold for lead in entity.leads.values())
