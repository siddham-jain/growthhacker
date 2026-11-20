import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import regex

from app.core.config import TopicEngineSettings
from app.services.topics.text.normalize import clean_text, fold

URL = regex.compile(r"(?:https?://|www\.)\S+", regex.IGNORECASE)
DIGITS = regex.compile(r"\d+")
SPACES = regex.compile(r"\s+")
EDGE_PUNCT = regex.compile(r"^[\p{P}\p{S}\s]+|[\p{P}\p{S}\s]+$")
SENTENCE = regex.compile(r"(?<=[.!?।])\s+")
HASHTAG = regex.compile(r"#[\p{L}\p{M}\p{N}_]+")
LONG_LINE = 200
UNPROFILED = "unprofiled"


def line_key(line: str) -> str:
    key = URL.sub("<url>", fold(clean_text(line)))
    key = DIGITS.sub("0", key)
    return EDGE_PUNCT.sub("", SPACES.sub(" ", key)).strip()


def segments(line: str) -> list[tuple[int, str]]:
    """a description line, split into sentences when it is long; (offset in line, text)."""
    if len(line) <= LONG_LINE:
        return [(0, line)]
    parts, offset = [], 0
    for piece in SENTENCE.split(line):
        start = line.index(piece, offset)
        parts.append((start, piece))
        offset = start + len(piece)
    return parts


@dataclass(frozen=True)
class BoilerplateProfile:
    channel_id: str
    version: str
    line_keys: frozenset[str] = field(default_factory=frozenset)
    hashtags: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    sample_size: int = 0

    @property
    def profiled(self) -> bool:
        return self.version != UNPROFILED

    @classmethod
    def unprofiled(cls, channel_id: str, sample_size: int = 0) -> "BoilerplateProfile":
        return cls(channel_id=channel_id, version=UNPROFILED, sample_size=sample_size)


@dataclass(frozen=True)
class ChannelSample:
    description: str
    tags: Sequence[str]
    title: str = ""


class ChannelBoilerplateProfiler:
    """lines, hashtags and tags repeated across a channel's recent uploads are channel furniture."""

    def __init__(self, settings: TopicEngineSettings) -> None:
        self._settings = settings

    def build(self, channel_id: str, samples: Sequence[ChannelSample]) -> BoilerplateProfile:
        samples = list(samples)[: self._settings.bp_sample]
        if len(samples) < self._settings.bp_min_videos:
            return BoilerplateProfile.unprofiled(channel_id, len(samples))
        line_df: Counter[str] = Counter()
        hashtag_df: Counter[str] = Counter()
        tag_df: Counter[str] = Counter()
        for sample in samples:
            keys = {
                line_key(text)
                for line in (sample.description or "").split("\n")
                for _, text in segments(line)
            }
            line_df.update(key for key in keys if key)
            hashtag_df.update({fold(tag) for tag in HASHTAG.findall(f"{sample.title}\n{sample.description}")})
            tag_df.update({fold(clean_text(tag)) for tag in sample.tags if tag.strip()})
        size = len(samples)
        line_keys = frozenset(key for key, df in line_df.items() if df / size >= self._settings.bp_line_df)
        hashtags = frozenset(
            key for key, df in hashtag_df.items() if df / size >= self._settings.bp_hashtag_df
        )
        tags = frozenset(key for key, df in tag_df.items() if df / size >= self._settings.bp_tag_df)
        digest = hashlib.sha256(
            "\n".join([*sorted(line_keys), "#", *sorted(hashtags), "#", *sorted(tags)]).encode()
        ).hexdigest()[:16]
        return BoilerplateProfile(channel_id, digest, line_keys, hashtags, tags, size)


class BoilerplateStripper:
    def __init__(self, settings: TopicEngineSettings) -> None:
        self._block_share = settings.bp_block_share

    def boilerplate_flags(self, keys: Sequence[str], profile: BoilerplateProfile) -> list[bool]:
        """per segment key: true when it is channel boilerplate (incl. the tail of a footer block)."""
        flags = [bool(key) and key in profile.line_keys for key in keys]
        for index in range(1, len(flags)):
            if flags[index] and flags[index - 1]:
                tail = flags[index - 1 :]
                if sum(tail) / len(tail) >= self._block_share:
                    return flags[: index - 1] + [True] * len(tail)
        return flags
