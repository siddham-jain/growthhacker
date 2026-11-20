from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.youtube import QuotaBucket


def _local(at: datetime, timezone: str) -> str:
    return at.astimezone(ZoneInfo(timezone)).strftime("%d %b %H:%M %Z")


def quota_exhausted(
    bucket: QuotaBucket, data_as_of: datetime | None, resets_at: datetime, timezone: str
) -> str:
    reset = _local(resets_at, timezone)
    if bucket is QuotaBucket.search:
        return f"Niche search is paused until {reset}. Results use earlier cached searches."
    as_of = _local(data_as_of, timezone) if data_as_of else "the last refresh"
    return f"We've hit today's YouTube data limit. Showing data as of {as_of}; fresh data after {reset}."


def handle_not_found(handle: str) -> str:
    return f"We couldn't find @{handle.lstrip('@')}. Paste a link to any video from your channel."


CUSTOM_URL = "Old-style /c/ links can't always be looked up. Paste a video link instead."
UNKNOWN_INPUT = (
    "That doesn't look like a YouTube channel or video link. Paste any video link from the channel."
)
FORBIDDEN = "This channel's data isn't available through YouTube's API."
NO_UPLOADS = "This channel has no public videos yet."
VERY_NEW = "Not enough of your own videos for a baseline yet; we compare against similar channels instead."
PARTIAL = "Some YouTube data couldn't load; we'll retry automatically."
KEY_REJECTED = "YouTube data is temporarily unavailable. Our team has been alerted."


def from_video(title: str) -> str:
    return f"Found channel '{title}' from that video."


def channel_gone(title: str) -> str:
    return f"{title} is no longer available on YouTube."


def shorts_only() -> str:
    return "All your recent videos are Shorts; long-form ideas are compared with competitors only."


def ownership_mismatch(handle: str | None, title: str, subscribers: int | None, domain: str | None) -> str:
    who = f"@{handle.lstrip('@')}" if handle else "This channel"
    scale = f" ({subscribers:,} subscribers)" if subscribers is not None else ""
    site = domain or "your website"
    return f"{who} is '{title}'{scale} and doesn't link to {site}. Is this your channel?"
