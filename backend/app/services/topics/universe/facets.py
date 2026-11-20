from collections.abc import Collection, Sequence

from app.domain.profile import FunnelStage
from app.domain.topics import Facet
from app.services.topics.lexicon import Lexicons, PhraseSet
from app.services.topics.text.tokenize import Token, stems, tokenize

LOCALITY_CUE = "near me"


class FacetExtractor:
    """lexicon-driven facets, entity terms and locality terms for a buyer question."""

    def __init__(self, lexicons: Lexicons, locations: Collection[str] = ()) -> None:
        self._lexicons = lexicons
        self._locations = PhraseSet([LOCALITY_CUE, *locations])

    def facets(self, text: str) -> list[Facet]:
        keys = stems(text)
        return [facet for facet, cues in self._lexicons.facets.items() if cues.contains(keys)]

    def facet_spans(self, tokens: Sequence[Token]) -> dict[Facet, list[tuple[int, int]]]:
        keys = [token.stem for token in tokens]
        spans = {}
        for facet, cues in self._lexicons.facets.items():
            found = cues.find(keys)
            if found:
                spans[facet] = found
        return spans

    def locality_terms(self, text: str) -> list[str]:
        tokens = tokenize(text)
        keys = [token.stem for token in tokens]
        return [
            " ".join(token.key for token in tokens[start : start + length])
            for start, length in self._locations.find(keys)
        ]

    def core_entity_terms(self, text: str) -> list[str]:
        tokens = tokenize(text)
        excluded: set[int] = set()
        for spans in self.facet_spans(tokens).values():
            for start, length in spans:
                excluded.update(range(start, start + length))
        keys = [token.stem for token in tokens]
        for start, length in self._locations.find(keys):
            excluded.update(range(start, start + length))
        lexicons = self._lexicons
        terms = [
            token.key
            for index, token in enumerate(tokens)
            if index not in excluded
            and not token.is_number
            and token.stem not in lexicons.stopwords
            and token.stem not in lexicons.question_words
        ]
        return list(dict.fromkeys(terms))

    def funnel_stage(self, facets: Collection[Facet]) -> FunnelStage | None:
        # the latest funnel stage any facet points to wins
        order = [
            FunnelStage.awareness,
            FunnelStage.consideration,
            FunnelStage.decision,
            FunnelStage.post_purchase,
        ]
        stages = {stage for stage, members in self._lexicons.facet_stage if members & set(facets)}
        for stage in reversed(order):
            if stage in stages:
                return stage
        return None
