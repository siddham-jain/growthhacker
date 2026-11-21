from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.topics import TopicKind, TopicRef
from app.domain.trends import KeywordCandidate, KeywordSource
from app.services.news.text import contains_phrase, normalize

# single short words ("ENT") are too ambiguous for a youtube search
MIN_SINGLE_WORD_SEED = 4
SOURCE_ORDER = {KeywordSource.web: 0, KeywordSource.autocomplete: 1, KeywordSource.profile: 2}


@dataclass(frozen=True)
class WebKeyword:
    keyword: str
    topic_ref_id: str
    reason: str
    urls: list[str] = field(default_factory=list)


def searchable_seeds(
    refs: Sequence[TopicRef], limit: int, *, day: int = 0, brand_terms: Sequence[str] = ()
) -> list[TopicRef]:
    """offerings first, then keywords, rotating by day so every topic gets checked over a week.

    buyer questions are too long to seed youtube suggestions, and branded topics only find the client itself.
    """
    usable = [
        ref
        for ref in refs
        if ref.status == "active"
        and ref.kind in (TopicKind.offering, TopicKind.keyword)
        and (len(ref.label.split()) > 1 or len(ref.label) >= MIN_SINGLE_WORD_SEED)
        and not any(contains_phrase(ref.label, brand) for brand in brand_terms if brand)
    ]
    ranked = sorted(
        usable, key=lambda ref: (ref.kind is not TopicKind.offering, -ref.weight, ref.label.lower())
    )
    if len(ranked) <= limit:
        return ranked
    start = (day * limit) % len(ranked)
    return (ranked[start:] + ranked[:start])[:limit]


def brand_terms(name: str, aliases: Sequence[str]) -> list[str]:
    """the business name, its aliases and its leading word ("Apollo" of "Apollo Hospitals")."""
    words = name.split()
    lead = [words[0]] if len(words) > 1 and len(words[0]) >= MIN_SINGLE_WORD_SEED else []
    return list(dict.fromkeys([name, *aliases, *lead]))


def anchor_terms(ref: TopicRef) -> list[str]:
    return list(dict.fromkeys([ref.label, *(alias.text for alias in ref.aliases)]))


def is_anchored(keyword: str, ref: TopicRef) -> bool:
    return any(contains_phrase(keyword, term) for term in anchor_terms(ref))


class CandidateBuilder:
    """merges profile seeds, youtube suggestions and web keywords into one de-duplicated, ranked list."""

    def __init__(self, refs: Sequence[TopicRef]) -> None:
        self._refs = {ref.id: ref for ref in refs}
        self._by_key: dict[str, KeywordCandidate] = {}

    def _add(self, candidate: KeywordCandidate) -> None:
        key = normalize(candidate.keyword)
        if not key:
            return
        existing = self._by_key.get(key)
        if existing is None:
            self._by_key[key] = candidate
            return
        sources = sorted({*existing.sources, *candidate.sources}, key=SOURCE_ORDER.__getitem__)
        ranks = [r for r in (existing.autocomplete_rank, candidate.autocomplete_rank) if r is not None]
        self._by_key[key] = existing.model_copy(
            update={
                "sources": sources,
                "autocomplete_rank": min(ranks) if ranks else None,
                "web_reason": existing.web_reason or candidate.web_reason,
                "web_urls": list(dict.fromkeys([*existing.web_urls, *candidate.web_urls])),
            }
        )

    def add_seed(self, ref: TopicRef) -> None:
        self._add(
            KeywordCandidate(
                keyword=ref.label, sources=[KeywordSource.profile], topic_ref_id=ref.id, topic_label=ref.label
            )
        )

    def add_suggestions(self, ref: TopicRef, suggestions: Sequence[str], limit: int) -> None:
        kept = 0
        for rank, suggestion in enumerate(suggestions):
            if kept >= limit:
                break
            if normalize(suggestion) == normalize(ref.label) or not is_anchored(suggestion, ref):
                continue
            kept += 1
            self._add(
                KeywordCandidate(
                    keyword=suggestion,
                    sources=[KeywordSource.autocomplete],
                    topic_ref_id=ref.id,
                    topic_label=ref.label,
                    autocomplete_rank=rank,
                )
            )

    def add_web(self, keywords: Sequence[WebKeyword]) -> None:
        for item in keywords:
            ref = self._refs.get(item.topic_ref_id)
            # the llm must tie each web keyword to one of the client's topics; unknown ids are dropped
            if ref is None or not item.keyword.strip():
                continue
            self._add(
                KeywordCandidate(
                    keyword=item.keyword.strip(),
                    sources=[KeywordSource.web],
                    topic_ref_id=ref.id,
                    topic_label=ref.label,
                    web_reason=item.reason,
                    web_urls=item.urls,
                )
            )

    def ranked(self, limit: int) -> list[KeywordCandidate]:
        """keywords seen by more sources first, then web news, youtube suggestions, then plain profile seeds."""

        def key(candidate: KeywordCandidate) -> tuple:
            return (
                -len(candidate.sources),
                min(SOURCE_ORDER[s] for s in candidate.sources),
                candidate.autocomplete_rank if candidate.autocomplete_rank is not None else 99,
                candidate.keyword.lower(),
            )

        ordered = sorted(self._by_key.values(), key=key)
        # news-only phrases often match no video titles, so they get at most a third of the searches
        web_slots = max(1, limit // 3)
        # spread the search budget across topics before spending a second slot on any one
        picked: list[KeywordCandidate] = []
        per_topic: dict[str, int] = {}
        for round_cap in (1, 2, 99):
            for candidate in ordered:
                if len(picked) >= limit:
                    return picked
                if candidate in picked or per_topic.get(candidate.topic_ref_id, 0) >= round_cap:
                    continue
                if candidate.sources == [KeywordSource.web]:
                    if web_slots == 0:
                        continue
                    web_slots -= 1
                picked.append(candidate)
                per_topic[candidate.topic_ref_id] = per_topic.get(candidate.topic_ref_id, 0) + 1
        return picked
