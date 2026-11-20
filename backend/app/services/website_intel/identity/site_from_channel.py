import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.fetching import RawFetcher
from app.services.website_intel.inputs.ssrf import DnsLookupError, UnsafeTargetError
from app.services.website_intel.inputs.urls import InvalidUrlError, host_of, normalize_url, registrable_domain

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"')\]]+|(?:www\.)[a-z0-9.-]+\.[a-z]{2,}[^\s<>\"')\]]*", re.IGNORECASE
)
SHORTENER_DOMAINS = frozenset(
    {"bit.ly", "lnkd.in", "t.co", "linktr.ee", "tinyurl.com", "goo.gl", "rb.gy", "ow.ly"}
)
PLATFORM_DOMAINS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "google.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "whatsapp.com",
        "wa.me",
        "tiktok.com",
        "pinterest.com",
        "threads.net",
        "apple.com",
        "spotify.com",
        "telegram.me",
        "t.me",
        "amzn.to",
        "play.google.com",
    }
)
MAX_SHORT_LINKS = 5


@dataclass(frozen=True)
class SiteCandidate:
    domain: str
    url: str
    mentions: int
    in_channel_description: bool


def urls_in(text: str) -> list[str]:
    found = []
    for match in URL_PATTERN.findall(text or ""):
        try:
            found.append(normalize_url(match.rstrip(".,;:")))
        except InvalidUrlError:
            continue
    return found


class SiteFromChannel:
    """the data api has no about-page links, so the site comes from descriptions (plan 03 §5.2)."""

    def __init__(self, fetcher: RawFetcher) -> None:
        self._fetcher = fetcher

    async def candidate(
        self, channel_description: str, upload_descriptions: Sequence[str], ctx: FetchContext
    ) -> SiteCandidate | None:
        texts = [channel_description, *upload_descriptions[:50]]
        mentions: Counter[str] = Counter()
        first_url: dict[str, str] = {}
        in_channel: set[str] = set()
        expanded = 0
        for index, text in enumerate(texts):
            seen_here: set[str] = set()
            for url in urls_in(text):
                domain = registrable_domain(host_of(url))
                if domain in SHORTENER_DOMAINS and expanded < MAX_SHORT_LINKS:
                    expanded += 1
                    url = await self._expand(url, ctx) or url
                    domain = registrable_domain(host_of(url))
                if domain is None or domain in PLATFORM_DOMAINS or domain in SHORTENER_DOMAINS:
                    continue
                seen_here.add(domain)
                first_url.setdefault(domain, url)
            for domain in seen_here:
                mentions[domain] += 1
                if index == 0:
                    in_channel.add(domain)
        eligible = [domain for domain, count in mentions.items() if count >= 2 or domain in in_channel]
        if not eligible:
            return None
        best = max(eligible, key=lambda domain: (mentions[domain], domain in in_channel))
        return SiteCandidate(best, first_url[best], mentions[best], best in in_channel)

    async def _expand(self, url: str, ctx: FetchContext) -> str | None:
        try:
            raw = await self._fetcher.fetch_raw(url, ctx)
        except (UnsafeTargetError, DnsLookupError):
            return None
        return raw.final_url if raw.final_url != url else None
