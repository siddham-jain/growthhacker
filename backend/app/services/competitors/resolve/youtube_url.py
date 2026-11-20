import re
import unicodedata
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

from app.domain.common import DomainModel
from app.services.competitors.identity.normalize import registrable_domain

CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")
PLAYLIST_ID = re.compile(r"^(PL|UU|LL|FL|OL|RD)[0-9A-Za-z_-]{10,}$")
HANDLE_PUNCTUATION = frozenset("._-·")
YOUTUBE_HOSTS = frozenset({"youtube.com", "youtu.be", "youtube-nocookie.com"})
OTHER_PLATFORMS = frozenset(
    {
        "instagram.com",
        "tiktok.com",
        "x.com",
        "twitter.com",
        "linkedin.com",
        "facebook.com",
        "fb.com",
        "threads.net",
        "pinterest.com",
        "snapchat.com",
    }
)
RESERVED_PATHS = frozenset(
    {
        "watch",
        "results",
        "feed",
        "playlist",
        "redirect",
        "hashtag",
        "premium",
        "gaming",
        "account",
        "about",
        "t",
        "signin",
        "logout",
        "upload",
        "kids",
        "music",
        "post",
        "source",
        "s",
        "embed",
        "shorts",
        "live",
        "v",
        "attribution_link",
        "channel",
        "user",
        "c",
    }
)
VIDEO_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")


class YouTubeRef(DomainModel):
    kind: Literal["channel_id", "handle", "username", "custom", "video", "playlist"]
    value: str


class WebsiteRef(DomainModel):
    url: str
    domain: str


class NameRef(DomainModel):
    name: str


class RejectedRef(DomainModel):
    reason: str


ParsedInput = YouTubeRef | WebsiteRef | NameRef | RejectedRef


def parse_youtube_url(text: str) -> ParsedInput:
    raw = unicodedata.normalize("NFC", text.strip())
    if not raw:
        return RejectedRef(reason="empty input")
    if CHANNEL_ID.match(raw):
        return YouTubeRef(kind="channel_id", value=raw)
    if raw.startswith("@") and "/" not in raw:
        return _handle(raw[1:])
    if not _looks_like_url(raw):
        return NameRef(name=raw)
    url = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return RejectedRef(reason="not a web address")
    host = parts.hostname.lower()
    if host == "music.youtube.com":
        return RejectedRef(reason="youtube music links are not channels")
    domain = registrable_domain(host)
    if domain in YOUTUBE_HOSTS:
        return _parse_youtube(domain, parts.path, parts.query)
    if domain in OTHER_PLATFORMS:
        return RejectedRef(reason="other platforms are out of scope")
    if domain is None:
        return NameRef(name=raw)
    return WebsiteRef(url=url, domain=domain)


def _looks_like_url(raw: str) -> bool:
    if "://" in raw:
        return True
    if " " in raw:
        return False
    head = raw.split("/", 1)[0]
    return "." in head and registrable_domain(head) is not None


def _valid_handle(handle: str) -> bool:
    # handles allow letters of many scripts, so combining marks (devanagari vowel signs) are accepted too
    return 3 <= len(handle) <= 30 and all(
        char.isalnum() or char in HANDLE_PUNCTUATION or unicodedata.category(char).startswith("M")
        for char in handle
    )


def _handle(value: str) -> ParsedInput:
    handle = unicodedata.normalize("NFC", unquote(value).strip().rstrip("/"))
    if _valid_handle(handle):
        return YouTubeRef(kind="handle", value=handle)
    return RejectedRef(reason="not a valid youtube handle")


def _video(value: str) -> ParsedInput:
    video_id = value.strip()
    if VIDEO_ID.match(video_id):
        return YouTubeRef(kind="video", value=video_id)
    return RejectedRef(reason="not a valid youtube video link")


def _parse_youtube(domain: str, path: str, query: str) -> ParsedInput:
    params = parse_qs(query)
    segments = [unquote(segment) for segment in path.split("/") if segment]
    if domain == "youtu.be":
        return _video(segments[0]) if segments else RejectedRef(reason="incomplete youtu.be link")
    if domain == "youtube-nocookie.com":
        if len(segments) >= 2 and segments[0] == "embed":
            return _video(segments[1])
        return RejectedRef(reason="unsupported embed link")
    if not segments:
        return RejectedRef(reason="youtube home page is not a channel")
    head = segments[0]
    if head == "attribution_link" and "u" in params:
        target = params["u"][0]
        return _parse_youtube("youtube.com", *_split_relative(target))
    if head == "watch":
        if "v" in params:
            return _video(params["v"][0])
        if "list" in params:
            return _playlist(params["list"][0])
        return RejectedRef(reason="watch link without a video id")
    if head == "playlist" and "list" in params:
        return _playlist(params["list"][0])
    if head in VIDEO_PATH_PREFIXES and len(segments) >= 2:
        return _video(segments[1])
    if head.startswith("@"):
        return _handle(head[1:])
    if head == "channel" and len(segments) >= 2:
        if CHANNEL_ID.match(segments[1]):
            return YouTubeRef(kind="channel_id", value=segments[1])
        return RejectedRef(reason="not a valid channel id")
    if head == "user" and len(segments) >= 2:
        return YouTubeRef(kind="username", value=segments[1])
    if head == "c" and len(segments) >= 2:
        return YouTubeRef(kind="custom", value=segments[1])
    if head not in RESERVED_PATHS and len(segments) <= 2:
        return YouTubeRef(kind="custom", value=head)
    return RejectedRef(reason="unsupported youtube link")


def _split_relative(target: str) -> tuple[str, str]:
    parts = urlsplit(unquote(target))
    return parts.path, parts.query


def _playlist(value: str) -> ParsedInput:
    playlist_id = value.strip()
    if PLAYLIST_ID.match(playlist_id):
        return YouTubeRef(kind="playlist", value=playlist_id)
    return RejectedRef(reason="not a valid playlist link")
