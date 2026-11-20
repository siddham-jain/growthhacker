from dataclasses import dataclass

import regex

from app.services.answer_citations.models import AnswerSearchResult, CitedIn
from app.services.youtube.input_parser import InputParser, VideoHandler

ANSWER_URL = regex.compile(r"https?://[^\s<>()\[\]\"'`]+")
TRAILING_PUNCTUATION = ".,;:!?"
_VIDEO_URLS = InputParser((VideoHandler(),))


@dataclass(frozen=True)
class CitedUrl:
    video_id: str
    url: str
    position: int | None
    cited_in: CitedIn


def video_id_of(url: str) -> str | None:
    ref = _VIDEO_URLS.parse(url)
    return ref.value if ref.kind == "video" else None


def answer_urls(answer: str | None) -> list[str]:
    return [match.rstrip(TRAILING_PUNCTUATION) for match in ANSWER_URL.findall(answer or "")]


def cited_videos(result: AnswerSearchResult) -> list[CitedUrl]:
    """youtube videos among the answer's sources, then any linked in the answer text; first mention wins."""
    seen: dict[str, CitedUrl] = {}
    candidates: list[tuple[str, int | None, CitedIn]] = [
        (source.url, source.position, "result") for source in result.sources
    ]
    candidates += [(url, None, "answer") for url in answer_urls(result.answer)]
    for url, position, cited_in in candidates:
        video_id = video_id_of(url)
        if video_id is not None and video_id not in seen:
            seen[video_id] = CitedUrl(video_id=video_id, url=url, position=position, cited_in=cited_in)
    return list(seen.values())
