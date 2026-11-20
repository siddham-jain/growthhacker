import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.domain.profile import Evidence, Fact, FactKind
from app.domain.web import ExtractionKind
from app.services.website_intel.models import ExtractedPage

QUOTE_CHAR_CAP = 300
TEXT_CHAR_CAP = 160
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_MARKDOWN = re.compile(r"[*_`#>\[\]]+")
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def normalise_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(_QUOTES)
    return _SPACES.sub(" ", _MARKDOWN.sub(" ", text)).strip()


def numbers_in(text: str) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER.findall(text)}


@dataclass(frozen=True)
class Verification:
    score: float
    char_start: int | None


class EvidenceVerifier:
    """deterministic quote check: exact or near-exact substring, numbers preserved (plan 03 §5.9)."""

    def __init__(self, *, threshold: float = 92, min_quote_chars: int = 12) -> None:
        self.threshold = threshold
        self.min_quote_chars = min_quote_chars

    def verify(self, text: str, quote: str, page_text: str) -> Verification | None:
        if not quote.strip():
            return None
        page_norm = normalise_for_match(page_text)
        quote_norm = normalise_for_match(quote)
        if not numbers_in(text) <= numbers_in(quote):
            return None
        position = page_text.find(quote)
        if quote_norm and quote_norm in page_norm:
            return Verification(100.0, position if position >= 0 else None)
        if len(quote_norm) < self.min_quote_chars:
            return None
        score = fuzz.partial_ratio(quote_norm, page_norm)
        if score < self.threshold:
            return None
        return Verification(round(float(score), 2), None)


@dataclass
class FactStore:
    """verified facts with global F-ids; exact duplicates merge their evidence."""

    facts: dict[str, Fact] = field(default_factory=dict)
    extra_evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    _by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    dropped: int = 0

    def next_id(self) -> str:
        return f"F{len(self.facts) + 1:04d}"

    def add(
        self,
        page: ExtractedPage,
        *,
        kind: FactKind,
        text: str,
        quote: str,
        extraction: ExtractionKind,
        score: float = 100.0,
        char_start: int | None = None,
        attrs: dict[str, str] | None = None,
    ) -> str:
        key = (kind.value, normalise_for_match(text))
        evidence = Evidence(
            page_id=page.page.page_id,
            source_kind=page.page.source_kind,
            extraction=extraction,
            source_url=page.page.url,
            quote=quote[:QUOTE_CHAR_CAP],
            quote_lang=page.page.lang,
            char_start=char_start,
            match_score=score,
            fetched_at=page.page.fetched_at,
            snapshot_at=page.page.snapshot_at,
        )
        existing = self._by_key.get(key)
        if existing is not None:
            if all(item.source_url != evidence.source_url for item in self.evidence(existing)):
                self.extra_evidence.setdefault(existing, []).append(evidence)
            return existing
        fact_id = self.next_id()
        self.facts[fact_id] = Fact(
            **evidence.model_dump(),
            fact_id=fact_id,
            kind=kind,
            text=text[:TEXT_CHAR_CAP],
            attrs=attrs or {},
        )
        self._by_key[key] = fact_id
        return fact_id

    def evidence(self, fact_id: str) -> list[Evidence]:
        fact = self.facts.get(fact_id)
        if fact is None:
            return []
        primary = Evidence.model_validate(fact.model_dump(exclude={"fact_id", "kind", "text", "attrs"}))
        return [primary, *self.extra_evidence.get(fact_id, [])]

    def evidence_for(self, fact_ids: Iterable[str]) -> list[Evidence]:
        collected: list[Evidence] = []
        for fact_id in fact_ids:
            collected.extend(self.evidence(fact_id))
        return collected

    def of_kind(self, *kinds: FactKind) -> list[Fact]:
        return [fact for fact in self.facts.values() if fact.kind in kinds]
