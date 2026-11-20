import unicodedata
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit

import regex

from app.domain.youtube import ChannelRef

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
        "youtu.be",
        "www.youtu.be",
    }
)
CHANNEL_ID = regex.compile(r"^UC[0-9A-Za-z_-]{22}$")
VIDEO_ID = regex.compile(r"^[0-9A-Za-z_-]{11}$")
PLAYLIST_ID = regex.compile(r"^(?:PL|UU|UUSH|LL|FL|OL|RD)[0-9A-Za-z_-]{10,}$")
HANDLE = regex.compile(r"^[\p{L}\p{N}\p{M}_.\-·]{3,30}$")
VIDEO_PATHS = ("shorts", "live", "embed", "v", "e")
RESERVED_PATHS = frozenset(
    {
        "watch",
        "feed",
        "results",
        "playlist",
        "channel",
        "user",
        "c",
        "shorts",
        "live",
        "embed",
        "v",
        "e",
        "about",
        "account",
        "premium",
        "music",
        "gaming",
        "hashtag",
        "post",
        "redirect",
        "t",
        "signin",
    }
)
TRACKING_PARAMS = frozenset({"si", "feature", "pp", "utm_source", "utm_medium", "utm_campaign", "ab_channel"})


@dataclass(frozen=True)
class ParsedInput:
    raw: str
    text: str
    host: str | None
    segments: tuple[str, ...]
    query: dict[str, list[str]]


class UrlPatternHandler(Protocol):
    def match(self, parsed: ParsedInput) -> ChannelRef | None: ...


def _ref(kind: str, value: str, parsed: ParsedInput) -> ChannelRef:
    return ChannelRef(kind=kind, value=value, raw=parsed.raw)


class ChannelIdHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.host is None and CHANNEL_ID.match(parsed.text):
            return _ref("channel_id", parsed.text, parsed)
        if (
            parsed.segments[:1] == ("channel",)
            and len(parsed.segments) > 1
            and CHANNEL_ID.match(parsed.segments[1])
        ):
            return _ref("channel_id", parsed.segments[1], parsed)
        return None


class VideoHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.host is None:
            return None
        candidate = None
        if parsed.host.endswith("youtu.be") and parsed.segments:
            candidate = parsed.segments[0]
        elif parsed.segments[:1] == ("watch",) or (not parsed.segments and "v" in parsed.query):
            candidate = (parsed.query.get("v") or [""])[0]
        elif len(parsed.segments) > 1 and parsed.segments[0] in VIDEO_PATHS:
            candidate = parsed.segments[1]
        if candidate and VIDEO_ID.match(candidate):
            return _ref("video", candidate, parsed)
        return None


class PlaylistHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        playlist = (parsed.query.get("list") or [""])[0]
        if parsed.host is not None and PLAYLIST_ID.match(playlist):
            return _ref("playlist", playlist, parsed)
        return None


class HandleHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.host is None and parsed.text.startswith("@"):
            handle = parsed.text[1:]
        elif parsed.segments and parsed.segments[0].startswith("@"):
            handle = parsed.segments[0][1:]
        else:
            return None
        return _ref("handle", handle, parsed) if HANDLE.match(handle) else None


class UsernameHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.segments[:1] == ("user",) and len(parsed.segments) > 1:
            return _ref("username", parsed.segments[1], parsed)
        return None


class CustomUrlHandler:
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.host is None or parsed.host.endswith("youtu.be"):
            return None
        if parsed.segments[:1] == ("c",) and len(parsed.segments) > 1:
            return _ref("custom_url", parsed.segments[1], parsed)
        if len(parsed.segments) >= 1 and parsed.segments[0].casefold() not in RESERVED_PATHS:
            name = parsed.segments[0]
            return _ref("custom_url", name, parsed) if HANDLE.match(name) else None
        return None


class BareNameHandler:
    # a bare token is tried as a handle, then as a legacy username
    def match(self, parsed: ParsedInput) -> ChannelRef | None:
        if parsed.host is None and HANDLE.match(parsed.text):
            return _ref("handle", parsed.text, parsed)
        return None


DEFAULT_HANDLERS: tuple[UrlPatternHandler, ...] = (
    ChannelIdHandler(),
    VideoHandler(),
    PlaylistHandler(),
    HandleHandler(),
    UsernameHandler(),
    CustomUrlHandler(),
    BareNameHandler(),
)


class InputParser:
    """any channel input form to a ChannelRef; ids are case-sensitive, so only the host is lower-cased."""

    def __init__(self, handlers: tuple[UrlPatternHandler, ...] = DEFAULT_HANDLERS) -> None:
        self._handlers = handlers

    def normalize(self, raw: str) -> ParsedInput:
        text = unicodedata.normalize("NFC", unquote(raw.strip().strip("<>\"'"))).strip()
        lowered = text.casefold()
        looks_like_url = "://" in text or any(lowered.startswith(host) for host in YOUTUBE_HOSTS)
        if not looks_like_url:
            return ParsedInput(raw=raw, text=text, host=None, segments=(), query={})
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = (parts.hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            return ParsedInput(raw=raw, text=text, host="", segments=(), query={})
        segments = tuple(segment for segment in parts.path.split("/") if segment)
        query = {key: value for key, value in parse_qs(parts.query).items() if key not in TRACKING_PARAMS}
        return ParsedInput(raw=raw, text=text, host=host, segments=segments, query=query)

    def parse(self, raw: str) -> ChannelRef:
        parsed = self.normalize(raw)
        if parsed.host == "":
            return ChannelRef(kind="unknown", value=parsed.text, raw=raw)
        for handler in self._handlers:
            ref = handler.match(parsed)
            if ref is not None:
                return ref
        return ChannelRef(kind="unknown", value=parsed.text, raw=raw)
