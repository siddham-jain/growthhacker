from typing import Protocol

from rapidfuzz import fuzz

from app.domain.common import DomainModel
from app.services.website_intel.inputs.urls import InvalidUrlError, host_of, normalize_url, registrable_domain


class KbRelated(DomainModel):
    name: str
    website: str | None = None


class KbEntity(DomainModel):
    qid: str
    label: str
    description: str | None = None
    aliases: list[str] = []
    websites: list[str] = []
    industries: list[str] = []
    headquarters: list[str] = []
    countries: list[str] = []
    subsidiaries: list[KbRelated] = []
    parents: list[KbRelated] = []
    products: list[str] = []
    inception_year: int | None = None
    wikipedia_title: str | None = None
    wikipedia_lang: str = "en"

    @property
    def domains(self) -> list[str]:
        found = []
        for website in self.websites:
            try:
                domain = registrable_domain(host_of(normalize_url(website)))
            except InvalidUrlError:
                continue
            if domain and domain not in found:
                found.append(domain)
        return found


class KbSummary(DomainModel):
    title: str
    extract: str
    url: str


class KnowledgeBase(Protocol):
    async def search(self, name: str, *, language: str = "en", limit: int = 5) -> list[KbEntity]: ...

    async def summary(self, title: str, *, language: str = "en") -> KbSummary | None: ...


NAME_MATCH_MIN = 85


def pick_entity(candidates: list[KbEntity], *, name: str | None, domain: str | None) -> KbEntity | None:
    """the website domain decides when known; otherwise a close name match that has an official site."""
    if domain is not None:
        for candidate in candidates:
            if domain in candidate.domains:
                return candidate
        return None
    if not name:
        return None
    scored = [
        (fuzz.token_set_ratio(name.casefold(), candidate.label.casefold()), index, candidate)
        for index, candidate in enumerate(candidates)
        if candidate.websites
    ]
    scored = [item for item in scored if item[0] >= NAME_MATCH_MIN]
    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], -item[1]))[2]
