from dataclasses import dataclass
from typing import Protocol

from app.domain.profile import BusinessProfile
from app.domain.topics import TopicKind, TopicRef
from app.services.competitors.identity.normalize import normalize_text

MIN_PHRASE_CHARS = 3


@dataclass(frozen=True)
class TopicTerm:
    id: str
    phrases: tuple[str, ...]


class LexicalTopicMatcher(Protocol):
    # lexical only: matching stays anchored to the client's own terms and costs nothing at onboarding
    size: int

    def match(self, text: str) -> set[str]: ...


class PhraseTopicMatcher:
    def __init__(self, terms: list[TopicTerm]) -> None:
        self.terms = [term for term in terms if term.phrases]
        self.size = len(self.terms)

    @classmethod
    def from_topic_refs(cls, refs: list[TopicRef]) -> "PhraseTopicMatcher":
        terms = []
        for ref in refs:
            if ref.status != "active":
                continue
            if ref.kind is TopicKind.buyer_question:
                texts = ref.core_entity_terms
            else:
                texts = [ref.label, *(alias.text for alias in ref.aliases if alias.status != "rejected")]
            terms.append(TopicTerm(id=ref.id, phrases=_phrases(texts)))
        return cls(terms)

    @classmethod
    def from_profile(cls, profile: BusinessProfile) -> "PhraseTopicMatcher":
        terms = [
            TopicTerm(id=f"offering:{offering.id}", phrases=_phrases([offering.name, *offering.aliases]))
            for offering in profile.offerings
        ]
        terms.extend(
            TopicTerm(id=f"keyword:{keyword.id}", phrases=_phrases([keyword.term]))
            for keyword in profile.niche_keywords
        )
        return cls(terms)

    def match(self, text: str) -> set[str]:
        padded = f" {normalize_text(text)} "
        return {term.id for term in self.terms if any(f" {phrase} " in padded for phrase in term.phrases)}


def _phrases(texts: list[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(phrase for text in texts if len(phrase := normalize_text(text)) >= MIN_PHRASE_CHARS)
    )
