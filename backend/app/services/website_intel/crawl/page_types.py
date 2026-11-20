import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.services.website_intel.crawl.templates import Gazetteer
from app.services.website_intel.models import PageType

LEXICON_DIR = Path(__file__).parent / "data"
DEFAULT_LANGUAGES = ("en", "hi", "hi-Latn")
MAX_EXTRA_SEGMENT_TOKENS = 2
MAX_TITLE_TOKENS = 6
_SEGMENT_SPLIT = re.compile(r"[-_.]+")
_TEXT_SPLIT = re.compile(r"[\s\-_/|,.:;!?()\[\]\"'&]+")
_TITLE_SEPARATOR = re.compile(r"\s[|\-–—:]\s")

HUB_TYPES: dict[str, tuple[PageType, PageType]] = {
    "offering": (PageType.offering_hub, PageType.offering_detail),
    "locations": (PageType.locations_hub, PageType.location_detail),
    "team": (PageType.team_hub, PageType.team_member),
    "blog": (PageType.blog_index, PageType.blog_post),
}

JSON_LD_TYPES: dict[str, PageType] = {
    "faqpage": PageType.faq,
    "aboutpage": PageType.about,
    "contactpage": PageType.contact,
    "physician": PageType.team_member,
    "person": PageType.team_member,
    "blogposting": PageType.blog_post,
    "article": PageType.blog_post,
    "newsarticle": PageType.news_press,
    "jobposting": PageType.careers,
    "product": PageType.offering_detail,
    "offer": PageType.offering_detail,
    "aggregateoffer": PageType.offering_detail,
    "service": PageType.offering_detail,
    "medicalprocedure": PageType.offering_detail,
    "medicalcondition": PageType.knowledge_library,
    "hospital": PageType.location_detail,
    "medicalclinic": PageType.location_detail,
    "dentist": PageType.location_detail,
    "localbusiness": PageType.location_detail,
    "store": PageType.location_detail,
}


def _tokens(term: str, splitter: re.Pattern[str]) -> tuple[str, ...]:
    return tuple(token for token in splitter.split(term.casefold()) if token)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


@dataclass(frozen=True)
class Lexicon:
    hubs: dict[str, tuple[tuple[str, ...], ...]]
    types: dict[PageType, tuple[tuple[str, ...], ...]]


@lru_cache(maxsize=8)
def load_lexicon(languages: tuple[str, ...] = DEFAULT_LANGUAGES) -> Lexicon:
    hubs: dict[str, set[tuple[str, ...]]] = {family: set() for family in HUB_TYPES}
    types: dict[PageType, set[tuple[str, ...]]] = {}
    for language in languages:
        data = json.loads((LEXICON_DIR / f"{language}.json").read_text(encoding="utf-8"))
        for family, terms in data["hubs"].items():
            hubs[family].update(_tokens(term, _TEXT_SPLIT) for term in terms)
        for name, terms in data["types"].items():
            types.setdefault(PageType(name), set()).update(_tokens(term, _TEXT_SPLIT) for term in terms)
    return Lexicon(
        hubs={family: tuple(sorted(terms)) for family, terms in hubs.items()},
        types={page_type: tuple(sorted(terms)) for page_type, terms in types.items()},
    )


class PageTypeClassifier:
    """rule-based: url segments right to left, then json-ld @type, then anchor, h1 and title (plan 03 §5.5)."""

    def __init__(
        self, languages: Sequence[str] = DEFAULT_LANGUAGES, gazetteer: Gazetteer | None = None
    ) -> None:
        self.lexicon = load_lexicon(tuple(languages))
        self.gazetteer = gazetteer

    def classify(
        self,
        url: str,
        *,
        anchor: str = "",
        title: str = "",
        h1: str = "",
        json_ld_types: Sequence[str] = (),
        is_home: bool = False,
    ) -> PageType:
        segments = [unquote(segment) for segment in urlsplit(url).path.split("/") if segment]
        if is_home or not segments:
            return PageType.home
        from_url = self._from_segments(segments)
        if from_url is not None:
            return from_url
        for json_ld_type in json_ld_types:
            mapped = JSON_LD_TYPES.get(json_ld_type.casefold())
            if mapped is not None:
                return mapped
        for text in (anchor, h1, self._title_head(title)):
            from_text = self._from_text(text)
            if from_text is not None:
                return from_text
        if self.gazetteer is not None and len(segments) <= 2 and self.gazetteer.contains(segments[-1]):
            return PageType.location_detail
        return PageType.other

    def _from_segments(self, segments: list[str]) -> PageType | None:
        last = len(segments) - 1
        for index in range(last, -1, -1):
            tokens = _tokens(segments[index], _SEGMENT_SPLIT)
            best: tuple[int, PageType] | None = None
            for family, terms in self.lexicon.hubs.items():
                width = self._segment_match_width(tokens, terms)
                if width and (best is None or width > best[0]):
                    hub, detail = HUB_TYPES[family]
                    best = (width, hub if index == last else detail)
            for page_type, terms in self.lexicon.types.items():
                width = self._segment_match_width(tokens, terms)
                if width and (best is None or width > best[0]):
                    best = (width, page_type)
            if best is not None:
                return best[1]
        return None

    @staticmethod
    def _segment_match_width(tokens: tuple[str, ...], terms: tuple[tuple[str, ...], ...]) -> int:
        # longest matching term wins, so "patient-stories" beats the bare "stories" hub term
        return max(
            (
                len(term)
                for term in terms
                if len(tokens) - len(term) <= MAX_EXTRA_SEGMENT_TOKENS and _contains(tokens, term)
            ),
            default=0,
        )

    def _from_text(self, text: str) -> PageType | None:
        tokens = _tokens(text, _TEXT_SPLIT)
        if not tokens:
            return None
        for family, terms in self.lexicon.hubs.items():
            if any(_contains(tokens, term) for term in terms):
                return HUB_TYPES[family][0]
        for page_type, terms in self.lexicon.types.items():
            if any(_contains(tokens, term) for term in terms):
                return page_type
        return None

    @staticmethod
    def _title_head(title: str) -> str:
        head = _TITLE_SEPARATOR.split(title, maxsplit=1)[0] if title else ""
        return head if len(_tokens(head, _TEXT_SPLIT)) <= MAX_TITLE_TOKENS else ""
