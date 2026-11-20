import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import TopicEngineSettings
from app.core.registry import Registry
from app.domain.topics import MatchEvidence, PreparedVideoText, TopicRef, TopicUniverse
from app.services.topics.lexicon import Lexicons
from app.services.topics.universe.expansion.base import CorpusFrequencies

Pair = tuple[str, str]


@dataclass(frozen=True)
class MatcherResult:
    score: float
    evidence: list[MatchEvidence] = field(default_factory=list)
    flags: frozenset[str] = frozenset()
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class MatchContext:
    universe: TopicUniverse
    settings: TopicEngineSettings
    lexicons: Lexicons
    non_topical_terms: Sequence[str] = ()
    corpus: CorpusFrequencies = field(default_factory=CorpusFrequencies)
    candidates: dict[str, set[str]] = field(default_factory=dict)
    # video_id -> "no_embedder" | "unsupported_script" when the video got no semantic score
    semantic_missing: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.refs: list[TopicRef] = [ref for ref in self.universe.refs if ref.status == "active"]
        self.refs_by_id: dict[str, TopicRef] = {ref.id: ref for ref in self.refs}

    def add_candidate(self, video_id: str, ref_id: str) -> None:
        self.candidates.setdefault(video_id, set()).add(ref_id)

    def specificity(self, keys: Sequence[str]) -> float:
        # idf of the rarest token relative to a 1% document frequency; small corpora carry no signal
        corpus = self.corpus
        if corpus.documents < self.settings.corpus_min_docs or not keys:
            return 1.0
        idf_ref = math.log(100.0)
        rarest = max(math.log(corpus.documents / max(corpus.df.get(key, 0), 1)) for key in keys)
        return min(1.0, rarest / idf_ref)


@dataclass(frozen=True)
class MatcherDeps:
    settings: TopicEngineSettings
    lexicons: Lexicons
    router: Any = None
    embedding_cache: Any = None
    ref_vectors: Any = None


class Matcher(Protocol):
    name: str
    order: int

    async def score(
        self, videos: Sequence[PreparedVideoText], ctx: MatchContext
    ) -> dict[Pair, MatcherResult]: ...


matcher_registry: Registry[Callable[[MatcherDeps], Matcher]] = Registry("topic_matcher_registry")
