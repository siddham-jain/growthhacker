from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.topics import TopicMatch
from app.domain.youtube import VideoRecord
from app.services.refresh.structure import (
    DescriptionStructure,
    boilerplate_key,
    detect_language,
    parse_description,
)
from app.services.refresh.text import content_tokens, ngrams, raw_tokens, split_hashtag


def mine_boilerplate(descriptions: Iterable[str], min_share: float, min_descriptions: int) -> frozenset[str]:
    # a line repeated across a share of the channel's descriptions is footer, not video content
    texts = list(descriptions)
    counts: Counter[str] = Counter()
    for text in texts:
        keys = {boilerplate_key(line) for line in text.splitlines() if line.strip()}
        counts.update(key for key in keys if key)
    threshold = max(min_descriptions, min_share * len(texts))
    return frozenset(key for key, count in counts.items() if count >= threshold)


def boilerplate_lines(keys: frozenset[str], descriptions: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for text in descriptions:
        for line in text.splitlines():
            key = boilerplate_key(line)
            if key in keys and key not in seen:
                seen[key] = line
    return list(seen.values())


@dataclass(frozen=True, slots=True)
class VideoContentEvidence:
    video_id: str
    sources_used: tuple[str, ...]
    covered_terms: frozenset[str]
    covered_tokens: frozenset[str]
    covered_topic_refs: frozenset[str]
    topic_scores: Mapping[str, float]
    structure: DescriptionStructure
    language: str
    code_mixed: bool
    owner_confirmed_keywords: frozenset[str]

    @property
    def fit_score(self) -> float:
        return max(self.topic_scores.values(), default=0.0)


def build_evidence(
    video: VideoRecord,
    boilerplate: frozenset[str],
    matches: Iterable[TopicMatch],
    fit_threshold: float,
    owner_confirmed_keywords: frozenset[str] = frozenset(),
) -> VideoContentEvidence:
    structure = parse_description(video.description, boilerplate)
    content_lines = [
        line
        for index, line in enumerate(structure.lines)
        if index not in structure.boilerplate_lines and line.strip()
    ]
    sources = {
        "title": [video.title],
        "description": content_lines,
        "chapters": [chapter.label for chapter in structure.chapters],
        "tags": list(video.tags),
        "hashtags": [split_hashtag(tag) for tag in structure.hashtags],
    }
    terms: set[str] = set()
    used = []
    for name, texts in sources.items():
        grams = set().union(*(ngrams(raw_tokens(text)) for text in texts)) if texts else set()
        if grams:
            used.append(name)
            terms |= grams
    if owner_confirmed_keywords:
        used.append("owner_confirmation")
    covered_tokens = {
        token for text in (video.title, *content_lines, *video.tags) for token in content_tokens(text)
    }
    covered_tokens |= {token for tag in structure.hashtags for token in content_tokens(split_hashtag(tag))}
    scores: dict[str, float] = {}
    for match in matches:
        if match.accepted and match.combined_score >= fit_threshold:
            scores[match.topic_ref_id] = max(scores.get(match.topic_ref_id, 0.0), match.combined_score)
    language, code_mixed = detect_language(
        " ".join([video.title, *content_lines]), video.default_language or video.default_audio_language
    )
    return VideoContentEvidence(
        video_id=video.video_id,
        sources_used=tuple(used),
        covered_terms=frozenset(terms),
        covered_tokens=frozenset(covered_tokens),
        covered_topic_refs=frozenset(scores),
        topic_scores=scores,
        structure=structure,
        language=language,
        code_mixed=code_mixed,
        owner_confirmed_keywords=owner_confirmed_keywords,
    )
