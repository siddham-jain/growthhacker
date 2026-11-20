import re
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from urllib.parse import urlsplit

from lxml import etree

DEFAULT_SITEMAP_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/wp-sitemap.xml",
    "/sitemap.txt",
)
MAX_URLS_PER_FILE = 50_000
MAX_SITEMAP_BYTES = 50_000_000
NEGATIVE_SAMPLE_PER_GROUP = 2
POSITIVE_HINTS = re.compile(r"page|service|product|location|treatment|speciali|package|pricing|category")
NEGATIVE_HINTS = re.compile(r"product-variant|doctor|author|tag|post|blog|news|image|video")
_GZIP_MAGIC = b"\x1f\x8b"
_CHUNK = 64 * 1024


@dataclass
class SitemapEntry:
    url: str
    lastmod: datetime | None = None
    priority: float | None = None
    changefreq: str | None = None


@dataclass
class SitemapParse:
    entries: list[SitemapEntry] = field(default_factory=list)
    children: list[SitemapEntry] = field(default_factory=list)
    dropped_off_host: int = 0
    truncated: bool = False


class SitemapTooLarge(Exception):
    pass


def parse_lastmod(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            try:
                day = date.fromisoformat(text[:10])
            except ValueError:
                return None
            parsed = datetime(day.year, day.month, day.day)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_priority(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(max(float(value.strip()), 0.0), 1.0)
    except ValueError:
        return None


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


class SitemapReader:
    """streaming sitemap parser (sitemaps.org): urlset, sitemapindex, gzip, text, rss and atom."""

    def __init__(
        self, *, max_urls_per_file: int = MAX_URLS_PER_FILE, max_bytes: int = MAX_SITEMAP_BYTES
    ) -> None:
        self.max_urls_per_file = max_urls_per_file
        self.max_bytes = max_bytes

    def parse(self, content: bytes, *, url: str, allowed_hosts: set[str]) -> SitemapParse:
        try:
            data = self._decompress(content)
        except (SitemapTooLarge, zlib.error):
            return SitemapParse(truncated=True)
        result = SitemapParse()
        hosts = {host.lower() for host in allowed_hosts}
        head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
        if head.startswith(b"<"):
            self._parse_xml(data, result, hosts)
        else:
            self._parse_text(data, result, hosts)
        return result

    def _decompress(self, content: bytes) -> bytes:
        if not content.startswith(_GZIP_MAGIC):
            if len(content) > self.max_bytes:
                raise SitemapTooLarge
            return content
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = bytearray()
        pending = content
        while pending:
            output += inflater.decompress(pending, _CHUNK)
            if len(output) > self.max_bytes:
                raise SitemapTooLarge
            pending = inflater.unconsumed_tail
        output += inflater.flush()
        if len(output) > self.max_bytes:
            raise SitemapTooLarge
        return bytes(output)

    def _accept(
        self, entry: SitemapEntry, target: list[SitemapEntry], result: SitemapParse, hosts: set[str]
    ) -> bool:
        if (urlsplit(entry.url).hostname or "").lower() not in hosts:
            result.dropped_off_host += 1
            return True
        if len(result.entries) + len(result.children) >= self.max_urls_per_file:
            result.truncated = True
            return False
        target.append(entry)
        return True

    def _parse_text(self, data: bytes, result: SitemapParse, hosts: set[str]) -> None:
        for line in data.decode("utf-8", errors="replace").splitlines():
            candidate = line.strip()
            if candidate.startswith(("http://", "https://")) and not self._accept(
                SitemapEntry(candidate), result.entries, result, hosts
            ):
                return

    def _parse_xml(self, data: bytes, result: SitemapParse, hosts: set[str]) -> None:
        events = etree.iterparse(
            BytesIO(data),
            events=("end",),
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
            recover=True,
        )
        try:
            for _, element in events:
                tag = _local(element.tag)
                if tag in {"url", "sitemap", "item", "entry"}:
                    entry = self._entry(element, tag)
                    target = result.children if tag == "sitemap" else result.entries
                    if entry is not None and not self._accept(entry, target, result, hosts):
                        return
                    element.clear()
                    parent = element.getparent()
                    while parent is not None and element.getprevious() is not None:
                        del parent[0]
        except etree.XMLSyntaxError:
            result.truncated = True

    @staticmethod
    def _entry(element: etree._Element, tag: str) -> SitemapEntry | None:
        fields: dict[str, str] = {}
        for child in element:
            name = _local(child.tag)
            if name == "link" and tag == "entry":
                href = child.get("href")
                if href and child.get("rel", "alternate") == "alternate":
                    fields.setdefault("loc", href)
            elif child.text:
                fields.setdefault(name, child.text.strip())
        location = fields.get("loc") or fields.get("link")
        if not location:
            return None
        lastmod = (
            fields.get("lastmod") or fields.get("pubdate") or fields.get("updated") or fields.get("published")
        )
        return SitemapEntry(
            url=location.strip(),
            lastmod=parse_lastmod(lastmod),
            priority=_parse_priority(fields.get("priority")),
            changefreq=fields.get("changefreq"),
        )


def _newest_first(entries: list[SitemapEntry]) -> list[SitemapEntry]:
    oldest = datetime.min.replace(tzinfo=UTC)
    return sorted(
        entries, key=lambda entry: (entry.lastmod is not None, entry.lastmod or oldest), reverse=True
    )


def order_child_sitemaps(children: list[SitemapEntry], max_files: int) -> list[SitemapEntry]:
    """positive name hints first, neutral next, negative last; negatives are sampled when over the cap."""
    positive: list[SitemapEntry] = []
    neutral: list[SitemapEntry] = []
    negative: dict[str, list[SitemapEntry]] = defaultdict(list)
    for child in children:
        name = child.url.lower()
        negative_hit = NEGATIVE_HINTS.search(name)
        if negative_hit:
            negative[negative_hit.group(0)].append(child)
        elif POSITIVE_HINTS.search(name):
            positive.append(child)
        else:
            neutral.append(child)
    over_cap = len(children) > max_files
    tail: list[SitemapEntry] = []
    for group in negative.values():
        ordered = _newest_first(group)
        tail.extend(ordered[:NEGATIVE_SAMPLE_PER_GROUP] if over_cap else ordered)
    ordered = _newest_first(positive) + _newest_first(neutral) + _newest_first(tail)
    return ordered[:max_files]
