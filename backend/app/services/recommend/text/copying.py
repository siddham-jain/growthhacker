from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz, process, utils

from app.core.config import RecommendNoCopySettings


def normalise(text: str) -> str:
    # rapidfuzz's default processing, with runs of whitespace collapsed so punctuation can't hide a copy
    return " ".join(utils.default_process(text).split())


@dataclass(frozen=True, slots=True)
class CopyHit:
    rule: str
    title: str
    score: float


class CopyDetector:
    """flags text that copies a competitor or niche title, or repeats one of the client's own recent titles."""

    def __init__(self, others: Iterable[str], own: Iterable[str], settings: RecommendNoCopySettings) -> None:
        self.settings = settings
        self._others = self._prepare(others)
        self._own = self._prepare(own)
        self._others_exact = {processed: title for processed, title in self._others}
        self._own_exact = {processed: title for processed, title in self._own}

    @staticmethod
    def _prepare(titles: Iterable[str]) -> list[tuple[str, str]]:
        prepared = {}
        for title in titles:
            processed = normalise(title)
            if processed:
                prepared.setdefault(processed, title)
        return sorted(prepared.items())

    def check(self, candidate: str) -> CopyHit | None:
        text = normalise(candidate)
        if not text:
            return None
        if text in self._others_exact:
            return CopyHit("exact", self._others_exact[text], 100.0)
        if text in self._own_exact:
            return CopyHit("own_exact", self._own_exact[text], 100.0)
        return self._fuzzy(text) or self._own_repeat(text)

    def _fuzzy(self, text: str) -> CopyHit | None:
        settings = self.settings
        choices = [processed for processed, _ in self._others]
        if not choices:
            return None
        hit = process.extractOne(
            text, choices, scorer=fuzz.ratio, processor=None, score_cutoff=settings.ratio
        )
        if hit is not None:
            return CopyHit("ratio", self._others[hit[2]][1], hit[1])
        for choice, score, index in process.extract(
            text,
            choices,
            scorer=fuzz.token_sort_ratio,
            processor=None,
            score_cutoff=settings.token_sort_ratio,
            limit=None,
        ):
            length_ratio = len(text) / max(1, len(choice))
            if settings.length_ratio_min <= length_ratio <= settings.length_ratio_max:
                return CopyHit("token_sort_ratio", self._others[index][1], score)
        for choice, score, index in process.extract(
            text,
            choices,
            scorer=fuzz.partial_ratio,
            processor=None,
            score_cutoff=settings.partial_ratio,
            limit=None,
        ):
            shorter = text if len(text) <= len(choice) else choice
            if len(shorter.split()) >= settings.partial_min_tokens:
                return CopyHit("partial_ratio", self._others[index][1], score)
        return None

    def _own_repeat(self, text: str) -> CopyHit | None:
        choices = [processed for processed, _ in self._own]
        if not choices:
            return None
        hit = process.extractOne(
            text, choices, scorer=fuzz.ratio, processor=None, score_cutoff=self.settings.own_ratio
        )
        if hit is None:
            return None
        return CopyHit("own_ratio", self._own[hit[2]][1], hit[1])
