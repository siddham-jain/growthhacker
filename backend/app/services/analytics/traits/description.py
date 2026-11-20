from dataclasses import dataclass
from functools import cached_property

import regex
import tldextract

from app.core.registry import trait_extractor_registry
from app.domain.analytics import DescriptionTraits
from app.domain.youtube import VideoRecord
from app.services.analytics.traits.base import Features, TraitContext, flag
from app.services.analytics.traits.lexicons import load_lexicon
from app.services.analytics.traits.title import compile_any, phrase_pattern

URL = regex.compile(r"https?://[^\s<>\"']+|(?<![\w/@.])www\.[^\s<>\"']+", regex.IGNORECASE)
CHAPTER_LINE = regex.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.+?)\s*$")
HASHTAG = regex.compile(r"(?<!\w)#[\p{L}\p{N}_]+")
HANDLE = regex.compile(r"(?<![\w@])@[\w.\-]{3,30}")
TRAILING_PUNCTUATION = ".,;:!?)]}'\""
MALFORMED_SCHEME = regex.compile(
    r"\bh?tt?ps?:/(?!/)|\bhttps?//|\bhttps?:\\\\|\bhttps?://(?=\s|$)", regex.IGNORECASE
)
MAX_COUNTED_HASHTAGS = 60
MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_S = 10

# offline public-suffix snapshot; descriptions never trigger a network call
_DOMAINS = tldextract.TLDExtract(suffix_list_urls=())


def registrable_domain(url: str) -> str | None:
    host = url.strip(TRAILING_PUNCTUATION)
    parts = _DOMAINS(host)
    if not parts.domain or not parts.suffix:
        return None
    return f"{parts.domain}.{parts.suffix}".lower()


def _seconds(stamp: str) -> int:
    total = 0
    for piece in stamp.split(":"):
        total = total * 60 + int(piece)
    return total


def malformed_link_count(text: str, urls: list[str]) -> int:
    # broken schemes plus url-shaped tokens without a registrable domain
    unparseable = sum(1 for url in urls if registrable_domain(url) is None)
    return unparseable + len(MALFORMED_SCHEME.findall(text))


@dataclass(frozen=True, slots=True)
class DescriptionPart:
    traits: DescriptionTraits
    malformed_links: int


@dataclass(frozen=True, slots=True)
class Chapters:
    valid: bool
    titles: tuple[str, ...]


def parse_chapters(description: str) -> Chapters:
    # youtube's rules: first stamp at 0:00, at least three ascending stamps, each at least 10 s apart
    stamps: list[tuple[int, str]] = []
    for line in description.splitlines():
        match = CHAPTER_LINE.match(line)
        if match:
            stamps.append((_seconds(match.group(1)), match.group(2)))
    if len(stamps) < MIN_CHAPTERS or stamps[0][0] != 0:
        return Chapters(False, ())
    gaps = [later[0] - earlier[0] for earlier, later in zip(stamps, stamps[1:], strict=False)]
    if any(gap < MIN_CHAPTER_GAP_S for gap in gaps):
        return Chapters(False, ())
    return Chapters(True, tuple(title for _, title in stamps))


class DescriptionLexicon:
    def __init__(self) -> None:
        self.data = load_lexicon("description")
        self.version = self.data["version"]

    @cached_property
    def booking(self) -> regex.Pattern[str]:
        return compile_any(self.data["booking_cta"])

    @cached_property
    def phone(self) -> regex.Pattern[str]:
        return compile_any(self.data["phone"])

    @cached_property
    def whatsapp(self) -> regex.Pattern[str]:
        return compile_any(self.data["whatsapp"])

    @cached_property
    def paid(self) -> regex.Pattern[str]:
        return compile_any(self.data["paid_disclosure"])

    @cached_property
    def collab(self) -> regex.Pattern[str]:
        return compile_any(self.data["collab"])

    @cached_property
    def shorteners(self) -> frozenset[str]:
        return frozenset(self.data["shorteners"])

    @cached_property
    def youtube(self) -> frozenset[str]:
        return frozenset(self.data["youtube_domains"])

    @cached_property
    def social(self) -> frozenset[str]:
        return frozenset(self.data["social_domains"])


@trait_extractor_registry.register("description")
class DescriptionTraitExtractor:
    key = "description"
    scope = "intrinsic"

    def __init__(self) -> None:
        self.lexicon = DescriptionLexicon()
        self.version = self.lexicon.version

    def extract(self, video: VideoRecord, ctx: TraitContext) -> DescriptionPart:
        text = video.description or ""
        lex = self.lexicon
        urls = [match.group() for match in URL.finditer(text)]
        domains = [d for d in (registrable_domain(u) for u in urls) if d]
        unique_domains = sorted(set(domains))
        client_domains = {d.lower() for d in ctx.client.website_domains}
        own_domains = client_domains if ctx.is_client_video else {d.lower() for d in ctx.channel_domains}
        chapters = parse_chapters(text)
        hashtags = HASHTAG.findall(text)
        short_codes = phrase_pattern(ctx.client.phone_short_codes)
        traits = DescriptionTraits(
            char_len=len(text),
            links_count=len(urls),
            link_domains=unique_domains,
            has_external_link=any(d not in lex.youtube and d not in lex.social for d in domains),
            has_own_site_link=bool(own_domains & set(domains)),
            has_shortener_link=any(d in lex.shorteners for d in domains),
            has_booking_cta=bool(lex.booking.search(text)),
            has_phone=bool(lex.phone.search(text)) or bool(short_codes and short_codes.search(text)),
            has_whatsapp=bool(lex.whatsapp.search(text)),
            has_chapters=chapters.valid,
            chapter_count=len(chapters.titles),
            chapter_titles=list(chapters.titles),
            hashtag_count=len(hashtags),
            hashtags_ignored=len(hashtags) > MAX_COUNTED_HASHTAGS,
            has_paid_disclosure_text=bool(lex.paid.search(text)),
            mentioned_handles=sorted({h.rstrip(TRAILING_PUNCTUATION).lower() for h in HANDLE.findall(text)}),
            collab_markers=sorted({m.group().strip() for m in lex.collab.finditer(text)}),
            has_client_site_link=bool(client_domains & set(domains)),
            first_two_lines_have_client_keyword=self._head_has_keyword(text, ctx),
        )
        return DescriptionPart(traits=traits, malformed_links=malformed_link_count(text, urls))

    @staticmethod
    def _head_has_keyword(text: str, ctx: TraitContext) -> bool:
        pattern = phrase_pattern(ctx.client.keyword_terms)
        head = [line for line in text.splitlines() if line.strip()][:2]
        return bool(pattern and head and pattern.search("\n".join(head)))

    def mined(self, part: DescriptionPart) -> Features:
        booleans = (
            "has_external_link",
            "has_own_site_link",
            "has_shortener_link",
            "has_booking_cta",
            "has_phone",
            "has_whatsapp",
            "has_chapters",
            "has_paid_disclosure_text",
        )
        traits = part.traits
        features: Features = {f"description.{name}": flag(getattr(traits, name)) for name in booleans}
        features["description.has_collab_marker"] = flag(
            bool(traits.collab_markers or traits.mentioned_handles)
        )
        features["description.has_malformed_link"] = flag(part.malformed_links > 0)
        return features
