from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import TopicEngineSettings
from app.core.registry import Registry
from app.domain.topics import TopicAlias, TopicRef
from app.services.topics.lexicon import Lexicons


@dataclass(frozen=True)
class CorpusFrequencies:
    """document frequency of stem keys over the shared prepared-text corpus."""

    documents: int = 0
    df: Mapping[str, int] = field(default_factory=dict)

    def share(self, key: str) -> float:
        return self.df.get(key, 0) / self.documents if self.documents else 0.0


@dataclass(frozen=True)
class ExpansionContext:
    settings: TopicEngineSettings
    lexicons: Lexicons
    profile_texts: Sequence[str] = ()
    domain_kind: str = "none"
    corpus: CorpusFrequencies = field(default_factory=CorpusFrequencies)


class AliasExpander(Protocol):
    # deterministic rule expansion; every alias it returns is anchored to a client term of the ref
    name: str

    def expand(self, ref: TopicRef, ctx: ExpansionContext) -> list[TopicAlias]: ...


expander_registry: Registry[Callable[[], AliasExpander]] = Registry("topic_alias_expander_registry")
