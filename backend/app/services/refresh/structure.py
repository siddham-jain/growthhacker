from dataclasses import dataclass

import py3langid
import regex

from app.services.refresh.links.urls import URL, ExtractedUrl, extract_urls
from app.services.refresh.text import HASHTAG, normalise

SEPARATOR = regex.compile(r"^\s*[_\-=—–*~•.]{5,}\s*$")
TIMESTAMP = regex.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\b\s*[-–—:|)]?\s*(.*)$")
DISCLAIMER = regex.compile(
    r"(does not replace (professional )?medical advice|not a substitute for (professional )?medical advice"
    r"|for (general )?awareness (purposes )?only|for general awareness|consult your (doctor|physician)"
    r"|^\s*disclaimer\b)",
    regex.IGNORECASE,
)
DEVANAGARI = regex.compile(r"\p{Devanagari}")
LATIN = regex.compile(r"\p{Latin}")
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10


@dataclass(frozen=True, slots=True)
class Chapter:
    seconds: int
    label: str


@dataclass(frozen=True, slots=True)
class DescriptionStructure:
    text: str
    lines: tuple[str, ...]
    newline: str
    first_paragraph_end_line: int | None
    links_block_start_line: int | None
    hashtag_line: int | None
    separator_lines: tuple[int, ...]
    boilerplate_lines: frozenset[int]
    disclaimer_line: int | None
    chapters: tuple[Chapter, ...]
    chapter_problems: tuple[str, ...]
    hashtags: tuple[str, ...]
    urls: tuple[ExtractedUrl, ...]
    byte_length: int
    char_length: int

    @property
    def chapters_valid(self) -> bool:
        return bool(self.chapters) and not self.chapter_problems

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def only_boilerplate(self) -> bool:
        content = [index for index, line in enumerate(self.lines) if line.strip()]
        return bool(content) and all(index in self.boilerplate_lines for index in content)

    def line_offset(self, line_index: int) -> int:
        width = len(self.newline)
        return sum(len(line) + width for line in self.lines[:line_index])

    def disclaimer_text(self) -> str | None:
        return None if self.disclaimer_line is None else self.lines[self.disclaimer_line]


def boilerplate_key(line: str) -> str:
    return normalise(URL.sub(lambda match: match.group(0).split("?")[0], line))


def _parse_seconds(stamp: str) -> int:
    parts = [int(part) for part in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _chapters(lines: tuple[str, ...]) -> tuple[tuple[Chapter, ...], tuple[str, ...]]:
    chapters = []
    for line in lines:
        match = TIMESTAMP.match(line)
        if match:
            chapters.append(Chapter(seconds=_parse_seconds(match.group(1)), label=match.group(2).strip()))
    if not chapters:
        return (), ()
    problems = []
    if chapters[0].seconds != 0:
        problems.append("the first timestamp must be 00:00")
    if len(chapters) < MIN_CHAPTERS:
        problems.append(f"you need at least {MIN_CHAPTERS} timestamps")
    gaps = [later.seconds - earlier.seconds for earlier, later in zip(chapters, chapters[1:], strict=False)]
    if any(gap <= 0 for gap in gaps):
        problems.append("timestamps must go up in order")
    elif any(gap < MIN_CHAPTER_SECONDS for gap in gaps):
        problems.append(f"each chapter must be at least {MIN_CHAPTER_SECONDS} seconds long")
    return tuple(chapters), tuple(problems)


def _is_hashtag_line(line: str) -> bool:
    words = line.split()
    return bool(words) and sum(word.startswith("#") for word in words) / len(words) >= 0.6


def _is_url_line(line: str) -> bool:
    if not URL.search(line):
        return False
    remainder = URL.sub(" ", line)
    return len(remainder.split()) <= 4


def _links_block_start(
    lines: tuple[str, ...],
    first_paragraph_end: int | None,
    separators: tuple[int, ...],
    boilerplate: frozenset[int],
) -> int | None:
    floor = 0 if first_paragraph_end is None else first_paragraph_end + 1
    candidates = [index for index in (*separators, *boilerplate) if index >= floor]
    trailing_start = None
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip() or _is_hashtag_line(line):
            continue
        if _is_url_line(line):
            trailing_start = index
            continue
        break
    if trailing_start is not None and trailing_start >= floor:
        candidates.append(trailing_start)
    if all(index in boilerplate for index, line in enumerate(lines) if line.strip()) and boilerplate:
        return min(boilerplate)
    return min(candidates) if candidates else None


def parse_description(text: str, boilerplate_keys: frozenset[str] = frozenset()) -> DescriptionStructure:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = tuple(text.split(newline))
    first_paragraph_end = None
    for index, line in enumerate(lines):
        if line.strip():
            first_paragraph_end = index
        elif first_paragraph_end is not None:
            break
    separators = tuple(index for index, line in enumerate(lines) if SEPARATOR.match(line))
    boilerplate = frozenset(
        index
        for index, line in enumerate(lines)
        if line.strip() and boilerplate_key(line) in boilerplate_keys
    )
    if first_paragraph_end is not None and first_paragraph_end in boilerplate:
        first_paragraph_end = None
    hashtag_lines = [index for index, line in enumerate(lines) if _is_hashtag_line(line)]
    disclaimer = next((index for index, line in enumerate(lines) if DISCLAIMER.search(line)), None)
    chapters, problems = _chapters(lines)
    return DescriptionStructure(
        text=text,
        lines=lines,
        newline=newline,
        first_paragraph_end_line=first_paragraph_end,
        links_block_start_line=_links_block_start(lines, first_paragraph_end, separators, boilerplate),
        hashtag_line=hashtag_lines[-1] if hashtag_lines else None,
        separator_lines=separators,
        boilerplate_lines=boilerplate,
        disclaimer_line=disclaimer,
        chapters=chapters,
        chapter_problems=problems,
        hashtags=tuple(f"#{tag}" for tag in HASHTAG.findall(text)),
        urls=tuple(extract_urls(text, newline)),
        byte_length=len(text.encode("utf-8")),
        char_length=len(text),
    )


def detect_language(text: str, declared: str | None) -> tuple[str, bool]:
    # declared language wins; code-mixed latin + devanagari text is treated as english, templates only
    code_mixed = bool(DEVANAGARI.search(text)) and bool(LATIN.search(text))
    if declared:
        return declared.split("-")[0].lower(), code_mixed
    if not text.strip():
        return "en", False
    if code_mixed:
        return "en", True
    language, _ = py3langid.classify(text)
    return language, False
