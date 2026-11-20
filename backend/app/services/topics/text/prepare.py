import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

import regex

from app.core.config import TopicEngineSettings
from app.domain.topics import PreparedFields, PreparedVideoText, RemovedSpan
from app.services.topics.lexicon import Lexicons
from app.services.topics.text.boilerplate import (
    HASHTAG,
    URL,
    BoilerplateProfile,
    BoilerplateStripper,
    line_key,
    segments,
)
from app.services.topics.text.language import is_code_mixed
from app.services.topics.text.normalize import clean_text, fold
from app.services.topics.text.scripts import dominant_script, script_profile
from app.services.topics.text.tokenize import split_hashtag, stems, tokenize
from app.services.topics.versions import preparer_version

CHAPTER = regex.compile(r"^\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*[-–—:|]?\s*(.+)$")
YOUTUBE_URL = regex.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts|playlist|live|@)|youtu\.be/)\S*",
    regex.IGNORECASE,
)
INLINE_JUNK: tuple[tuple[str, regex.Pattern[str]], ...] = (
    ("url", URL),
    ("email", regex.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", regex.compile(r"(?<![\w])\+?\d[\d ().-]{7,}\d(?![\w])")),
    ("handle", regex.compile(r"(?<![\w@])@[\w.]{2,}")),
    ("timestamp", regex.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")),
)
LETTER = regex.compile(r"\p{L}")
MAX_CTA_TOKENS = 25
TAG_STUFFED_COUNT = 40
TAG_STUFFED_CHARS = 450
LONG_DESCRIPTION = 3000
BOILERPLATE_HEAVY = 0.8


@dataclass(frozen=True)
class VideoText:
    video_id: str
    channel_id: str
    title: str
    description: str
    tags: Sequence[str]
    content_hash: str
    default_language: str | None = None
    is_short: bool = False


@dataclass
class _DescriptionParts:
    kept: list[str] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    removed: list[RemovedSpan] = field(default_factory=list)


def prepared_hash(content_hash: str, boilerplate_version: str) -> str:
    return hashlib.sha256(f"{content_hash}|{boilerplate_version}|{preparer_version()}".encode()).hexdigest()


class VideoTextPreparer:
    """client-agnostic cleaning: fields, junk and boilerplate stripping, script and quality profile."""

    def __init__(
        self, settings: TopicEngineSettings, lexicons: Lexicons, supported_scripts: Collection[str]
    ) -> None:
        self._settings = settings
        self._lexicons = lexicons
        self._supported_scripts = frozenset(supported_scripts)
        self._stripper = BoilerplateStripper(settings)

    def prepare(self, video: VideoText, profile: BoilerplateProfile) -> PreparedVideoText:
        description = video.description or ""
        parts = self._description(description, profile)
        title, title_hashtags, title_removed = self._title(video.title or "", profile)
        hashtags = list(dict.fromkeys([*title_hashtags, *parts.hashtags]))
        body = "\n".join(parts.kept)
        head = _head(body, self._settings.head_chars)
        rest = body[len(head) :].strip()[: max(0, self._settings.desc_max_chars - len(head))]
        tags = self._tags(video.tags, profile)
        fields = PreparedFields(
            title=title,
            chapters=parts.chapters,
            description_head=head.strip(),
            description_rest=rest,
            tags=tags,
            hashtags=hashtags,
        )
        profile_text = "\n".join([title, *parts.chapters, body, *tags])
        scripts = script_profile(profile_text)
        tokens = tokenize(f"{title}\n{head}")
        flags = self._quality_flags(video, fields, parts, title_hashtags, scripts, tokens, profile)
        return PreparedVideoText(
            video_id=video.video_id,
            content_hash=video.content_hash,
            prepared_hash=prepared_hash(video.content_hash, profile.version),
            fields=fields,
            removed_spans=[*title_removed, *parts.removed],
            script_profile=scripts,
            code_mixed=is_code_mixed(scripts, tokens, self._lexicons),
            lang_hint=video.default_language,
            quality_flags=flags,
        )

    def _title(self, raw: str, profile: BoilerplateProfile) -> tuple[str, list[str], list[RemovedSpan]]:
        removed: list[RemovedSpan] = []
        kept, hashtags = self._strip_inline(raw, 0, "title", profile, removed)
        return clean_text(kept).replace("\n", " "), hashtags, removed

    def _description(self, raw: str, profile: BoilerplateProfile) -> _DescriptionParts:
        parts = _DescriptionParts()
        candidates: list[tuple[int, str]] = []
        offset = 0
        for raw_line in raw.split("\n"):
            line_start = offset
            offset += len(raw_line) + 1
            for segment_offset, segment in segments(raw_line):
                start = line_start + segment_offset
                text = clean_text(segment)
                if not text:
                    continue
                chapter = CHAPTER.match(text)
                if chapter:
                    label = clean_text(URL.sub(" ", chapter.group(1)))
                    if LETTER.search(label):
                        parts.chapters.append(label)
                    continue
                reason = self._line_reason(text)
                if reason:
                    parts.removed.append(
                        RemovedSpan(field="description", start=start, end=start + len(segment), reason=reason)
                    )
                    continue
                candidates.append((start, segment))
        flags = self._stripper.boilerplate_flags([line_key(text) for _, text in candidates], profile)
        for (start, segment), is_boilerplate in zip(candidates, flags, strict=True):
            if is_boilerplate:
                parts.removed.append(
                    RemovedSpan(
                        field="description", start=start, end=start + len(segment), reason="boilerplate"
                    )
                )
                continue
            kept, hashtags = self._strip_inline(segment, start, "description", profile, parts.removed)
            parts.hashtags.extend(hashtags)
            text = clean_text(kept)
            if LETTER.search(text) or any(char.isdigit() for char in text):
                parts.kept.append(text)
        parts.removed.sort(key=lambda span: span.start)
        return parts

    def _line_reason(self, text: str) -> str | None:
        folded = fold(text)
        if YOUTUBE_URL.search(text) or folded.startswith(self._lexicons.link_list_prefixes):
            return "link_list"
        keys = stems(text)
        if folded.startswith("disclaimer") or (
            len(keys) <= MAX_CTA_TOKENS and self._lexicons.cta.contains(keys)
        ):
            return "cta"
        return None

    def _strip_inline(
        self,
        text: str,
        base: int,
        field_name: str,
        profile: BoilerplateProfile,
        removed: list[RemovedSpan],
    ) -> tuple[str, list[str]]:
        chars = list(text)
        for reason, pattern in INLINE_JUNK:
            for match in pattern.finditer("".join(chars)):
                removed.append(
                    RemovedSpan(
                        field=field_name, start=base + match.start(), end=base + match.end(), reason=reason
                    )
                )
                chars[match.start() : match.end()] = [" "] * (match.end() - match.start())
        hashtags: list[str] = []
        current = "".join(chars)
        for match in HASHTAG.finditer(current):
            tag = match.group(0)
            if fold(tag) in profile.hashtags:
                removed.append(
                    RemovedSpan(
                        field=field_name,
                        start=base + match.start(),
                        end=base + match.end(),
                        reason="channel_hashtag",
                    )
                )
            else:
                hashtags.append(split_hashtag(tag, self._lexicons.hashtag_words))
            chars[match.start() : match.end()] = [" "] * (match.end() - match.start())
        return "".join(chars), hashtags

    def _tags(self, raw_tags: Sequence[str], profile: BoilerplateProfile) -> list[str]:
        tags: dict[str, str] = {}
        for raw in raw_tags:
            tag = clean_text(raw).replace("\n", " ")
            key = fold(tag)
            if tag and key not in profile.tags and key not in tags:
                tags[key] = tag
        return list(tags.values())

    def _quality_flags(
        self,
        video: VideoText,
        fields: PreparedFields,
        parts: _DescriptionParts,
        title_hashtags: list[str],
        scripts: dict[str, float],
        head_tokens: Sequence,
        profile: BoilerplateProfile,
    ) -> list[str]:
        lexicons = self._lexicons
        flags: list[str] = []
        raw_title = (video.title or "").strip()
        if raw_title and not LETTER.search(fields.title):
            flags.append("hashtag_only_title" if title_hashtags else "emoji_only_title")
        content = [
            token for token in head_tokens if not token.is_number and token.stem not in lexicons.stopwords
        ]
        if len(content) < 4:
            flags.append("very_short")
        all_text = " ".join(
            [
                fields.title,
                *fields.chapters,
                fields.description_head,
                fields.description_rest,
                *fields.tags,
                *fields.hashtags,
            ]
        )
        topical = [
            token
            for token in tokenize(all_text)
            if not token.is_number
            and token.stem not in lexicons.stopwords
            and token.stem not in lexicons.generic
        ]
        if not topical:
            flags.append("no_topical_text")
        description = video.description or ""
        removed_chars = sum(span.end - span.start for span in parts.removed)
        if len(description.strip()) >= 40 and removed_chars >= BOILERPLATE_HEAVY * len(description.strip()):
            flags.append("boilerplate_heavy")
        if len(video.tags) > TAG_STUFFED_COUNT or sum(len(tag) for tag in video.tags) > TAG_STUFFED_CHARS:
            flags.append("tag_stuffed")
        if len(description) > LONG_DESCRIPTION:
            flags.append("long_description")
        dominant = dominant_script(scripts)
        if dominant is not None and dominant not in self._supported_scripts:
            flags.append("unsupported_script")
        if not profile.profiled:
            flags.append("boilerplate_unprofiled")
        return flags


def _head(body: str, limit: int) -> str:
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    return cut[:space] if space > limit // 2 else cut
