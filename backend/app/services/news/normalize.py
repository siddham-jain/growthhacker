import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import py3langid
import tldextract

from app.domain.news import NewsItem, SourceTier
from app.ports.freshness import RawNewsItem
from app.services.news.packs import GlobalTiers
from app.services.news.tiers import SourceTierResolver, host_matches

NEWS_ITEM_NAMESPACE = uuid.UUID("6f0b6a4e-1c55-4b8e-9a51-3d8f2f0c8a08")
TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "cmpid", "ito", "__twitter_impression", "guccounter"}
)
HOST_PREFIXES = ("www.", "m.", "amp.")
CONTINUOUS_PATH = re.compile(r"/(releases|changelog|live|live-updates|liveblog)(/|$)")
PRESS_RELEASE_PATH = re.compile(r"/press-releases?(/|$)")
SUBSCRIBE_MARKER = re.compile(r"\bsubscribe\b|\bsubscribers only\b|\bpaywall\b", re.IGNORECASE)
MIN_LANGUAGE_CHARS = 20
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

Precision = Literal["datetime", "date", "none"]


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    for prefix in HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
    path = (parts.path or "").rstrip("/")
    if path.endswith("/amp"):
        path = path[: -len("/amp")]
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme.casefold() or "https", host, path, urlencode(sorted(query)), ""))


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    for prefix in HOST_PREFIXES:
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def registrable_domain(host: str) -> str:
    extracted = _EXTRACT(host)
    return extracted.top_domain_under_public_suffix or host


def date_precision(published_at: datetime | None) -> Precision:
    if published_at is None:
        return "none"
    return "date" if published_at.timetz().replace(tzinfo=None) == time(0, 0) else "datetime"


def effective_published_at(item: NewsItem) -> datetime | None:
    # date-only stamps count from midday, bounding the error to twelve hours
    if item.published_at is None:
        return None
    if item.published_precision == "date":
        return item.published_at + timedelta(hours=12)
    return item.published_at


def detect_language(text: str) -> str | None:
    if len(text.strip()) < MIN_LANGUAGE_CHARS:
        return None
    language, _score = py3langid.classify(text)
    return language


@dataclass(frozen=True)
class NormalizeOutcome:
    item: NewsItem | None
    dropped_reason: str | None = None


class ItemNormalizer:
    """raw provider records -> canonical global NewsItems (snippets only, never full text)."""

    def __init__(self, tiers: GlobalTiers, resolver: SourceTierResolver, future_tolerance: timedelta) -> None:
        self._tiers = tiers
        self._resolver = resolver
        self._future_tolerance = future_tolerance

    def _flags(self, host: str, path: str, raw: RawNewsItem, extra_paywalled: list[str]) -> list[str]:
        flags: list[str] = []
        if host_matches(host, self._tiers.paywalled + extra_paywalled) or (
            len(raw.snippet) < 200 and SUBSCRIBE_MARKER.search(raw.snippet)
        ):
            flags.append("paywalled")
        if host_matches(host, self._tiers.press_release) or PRESS_RELEASE_PATH.search(path):
            flags.append("press_release")
        if host_matches(host, self._tiers.social):
            flags.append("social")
        if host_matches(host, self._tiers.preprint) or raw.external_ids.get("pubtype") == "preprint":
            flags.append("preprint")
        if CONTINUOUS_PATH.search(path):
            flags.append("continuously_updated")
        return flags

    def normalize(
        self,
        raw: RawNewsItem,
        *,
        source_key: str,
        fetched_at: datetime,
        feed_tier: SourceTier | None = None,
        extra_paywalled: list[str] | None = None,
    ) -> NormalizeOutcome:
        host = host_of(raw.url)
        if not host or not raw.title.strip():
            return NormalizeOutcome(None, "malformed")
        if host_matches(host, self._tiers.excluded):
            return NormalizeOutcome(None, "excluded_domain")
        tier = feed_tier or self._resolver.resolve(host)
        if tier is SourceTier.blocked:
            return NormalizeOutcome(None, "blocked_domain")
        canonical = canonicalize_url(raw.url)
        flags = self._flags(host, urlsplit(canonical).path, raw, extra_paywalled or [])
        published_at = raw.published_at
        if published_at is not None and published_at > fetched_at + self._future_tolerance:
            published_at = fetched_at
            flags.append("future_date")
        hashed = url_hash(canonical)
        snippet = raw.snippet[:1_500]
        item = NewsItem(
            id=uuid.uuid5(NEWS_ITEM_NAMESPACE, hashed),
            source_key=source_key,
            url=raw.url,
            canonical_url=canonical,
            url_hash=hashed,
            domain=registrable_domain(host),
            title=raw.title.strip(),
            snippet=snippet,
            language=raw.language or detect_language(f"{raw.title} {snippet}"),
            published_at=published_at,
            published_precision="datetime" if "future_date" in flags else date_precision(published_at),
            fetched_at=fetched_at,
            source_rank_score=raw.provider_score,
            external_ids=dict(raw.external_ids),
            source_tier=tier,
            flags=flags,
        )
        return NormalizeOutcome(item)
