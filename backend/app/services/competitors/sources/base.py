from dataclasses import dataclass
from typing import Protocol

from app.ports.llm import LLMClient
from app.ports.search import WebSearch
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate
from app.services.competitors.youtube_access import MeteredYouTube


@dataclass(frozen=True)
class SourceDeps:
    youtube: MeteredYouTube
    web_search: WebSearch | None
    llm: LLMClient | None


class CandidateGenerator(Protocol):
    kind: str
    # rediscovery re-runs only the sources flagged here
    used_in_rediscovery: bool

    def enabled(self, ctx: DiscoveryContext) -> bool: ...

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]: ...
