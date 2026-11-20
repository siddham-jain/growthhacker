import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.services.website_intel.extract.cta import FormInfo
from app.services.website_intel.extract.sanitizer import normalize_text
from app.services.website_intel.inputs.urls import InvalidUrlError, normalize_url
from app.services.website_intel.models import Hreflang, LinkRegion, NavItem, PageLink

KEPT_JSON_LD_TYPES = frozenset(
    {
        "Organization",
        "Corporation",
        "MedicalOrganization",
        "Hospital",
        "MedicalClinic",
        "Physician",
        "LocalBusiness",
        "Product",
        "Service",
        "Offer",
        "AggregateOffer",
        "PriceSpecification",
        "UnitPriceSpecification",
        "FAQPage",
        "HowTo",
        "BreadcrumbList",
        "WebSite",
        "PostalAddress",
        "GeoCoordinates",
        "MedicalProcedure",
        "MedicalCondition",
        "MedicalSpecialty",
        "Dentist",
        "ProfessionalService",
        "SoftwareApplication",
        "Article",
        "NewsArticle",
        "BlogPosting",
    }
)
# local business subtypes are open-ended in schema.org, so suffixes stand in for the full list
KEPT_TYPE_SUFFIXES = ("Business", "Store", "Clinic", "Organization", "Service", "Establishment", "Agency")
COUNT_ONLY_TYPES = frozenset({"Physician"})
MAX_LINKS = 1500
MAX_NAV_ITEMS = 500
MAX_LABEL_CHARS = 120
NAV_CONTAINERS = "nav, [role=navigation], [class*=mega-menu], [class*=megamenu], [class*=mega_menu]"
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
# a string literal or a comment; strings are kept and comments dropped
_STRING_OR_COMMENT = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_WRAPPERS = re.compile(r"^\s*(?:<!--|<!\[CDATA\[)|(?:-->|\]\]>)\s*$")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.){20,})"')
_PROSE_WORDS = re.compile(r"\w+(?:\s+\w+){2,}")
# hydration keys whose short values still carry page content (plan names, prices, faq)
CONTENT_KEYS = frozenset(
    {
        "name",
        "title",
        "label",
        "heading",
        "headline",
        "price",
        "description",
        "text",
        "q",
        "a",
        "question",
        "answer",
    }
)
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
VIDEO_DATA_ATTRIBUTES = ("data-video-url", "data-video-src", "data-embed-url", "data-youtube-url")
NON_WEB_SCHEMES = ("tel:", "mailto:", "whatsapp:", "sms:")


@dataclass(frozen=True)
class MetaInfo:
    title: str | None = None
    description: str | None = None
    canonical: str | None = None
    lang: str | None = None
    og: dict[str, str] = field(default_factory=dict)
    hreflang: list[Hreflang] = field(default_factory=list)
    published_at: datetime | None = None

    def flat(self) -> dict[str, str]:
        values = dict(self.og)
        if self.description:
            values["description"] = self.description
        return values


def _types(entity: dict[str, Any]) -> list[str]:
    raw = entity.get("@type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [value for value in raw if isinstance(value, str)]
    return []


def _kept(entity: dict[str, Any]) -> bool:
    return any(name in KEPT_JSON_LD_TYPES or name.endswith(KEPT_TYPE_SUFFIXES) for name in _types(entity))


def _parse_lenient(raw: str) -> Any:
    text = _WRAPPERS.sub("", raw.strip())
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        without_comments = _STRING_OR_COMMENT.sub(
            lambda match: match.group(0) if match.group(0).startswith('"') else "", text
        )
        cleaned = _TRAILING_COMMA.sub(r"\1", without_comments)
        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            return None


def _flatten(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if graph is not None:
            yield from _flatten(graph)
        if "@type" in value:
            yield value


def _as_tree(html_or_tree: str | LexborHTMLParser) -> LexborHTMLParser:
    return LexborHTMLParser(html_or_tree) if isinstance(html_or_tree, str) else html_or_tree


def json_ld_blocks(html_or_tree: str | LexborHTMLParser) -> list[dict[str, Any]]:
    tree = _as_tree(html_or_tree)
    blocks: list[dict[str, Any]] = []
    for script in tree.css('script[type="application/ld+json"]'):
        parsed = _parse_lenient(script.text(deep=True) or "")
        for entity in _flatten(parsed):
            if not _kept(entity):
                continue
            if set(_types(entity)) & COUNT_ONLY_TYPES:
                blocks.append({"@type": "Physician"})
            else:
                blocks.append(entity)
    return blocks


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def published_at(meta_published: str | None, json_ld: list[dict[str, Any]]) -> datetime | None:
    found = _parse_datetime(meta_published)
    if found is not None:
        return found
    for entity in json_ld:
        value = entity.get("datePublished")
        if isinstance(value, str) and (found := _parse_datetime(value)) is not None:
            return found
    return None


def _absolute(href: str, base_url: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "data:")):
        return None
    if href.lower().startswith(NON_WEB_SCHEMES):
        return href
    try:
        return normalize_url(href, base=base_url)
    except InvalidUrlError:
        return None


def extract_meta(tree: LexborHTMLParser, base_url: str) -> MetaInfo:
    title_node = tree.css_first("title")
    og: dict[str, str] = {}
    description = None
    published = None
    for node in tree.css("meta"):
        key = (node.attributes.get("property") or node.attributes.get("name") or "").strip().lower()
        content = normalize_text(node.attributes.get("content") or "")
        if not key or not content:
            continue
        if key == "description":
            description = content
        elif key.startswith(("og:", "twitter:")):
            og.setdefault(key, content)
        elif key == "article:published_time":
            published = content
    canonical = None
    hreflang: list[Hreflang] = []
    for node in tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        href = _absolute(node.attributes.get("href") or "", base_url)
        if href is None or href.startswith(NON_WEB_SCHEMES):
            continue
        if "canonical" in rel and canonical is None:
            canonical = href
        lang = node.attributes.get("hreflang")
        if "alternate" in rel and lang:
            hreflang.append(Hreflang(lang=lang.strip(), url=href))
    html_node = tree.css_first("html")
    lang = (html_node.attributes.get("lang") or "").strip() if html_node is not None else ""
    title = normalize_text(title_node.text(deep=True)) if title_node is not None else ""
    return MetaInfo(
        title=title or None,
        description=description,
        canonical=canonical,
        lang=lang or None,
        og=og,
        hreflang=hreflang,
        published_at=_parse_datetime(published),
    )


def _anchor_label(node: LexborNode) -> str:
    label = normalize_text(node.text(deep=True, separator=" ") or "")
    if not label:
        label = normalize_text(node.attributes.get("aria-label") or node.attributes.get("title") or "")
    if not label:
        image = node.css_first("img[alt]")
        label = normalize_text(image.attributes.get("alt") or "") if image is not None else ""
    return label[:MAX_LABEL_CHARS]


def _region(node: LexborNode) -> LinkRegion:
    in_header = in_nav = False
    parent = node.parent
    while parent is not None and parent.is_element_node:
        tag = parent.tag
        role = (parent.attributes.get("role") or "").lower()
        if tag == "footer" or role == "contentinfo":
            return LinkRegion.footer
        if tag == "header" or role == "banner":
            in_header = True
        elif tag == "nav" or role == "navigation":
            in_nav = True
        parent = parent.parent
    if in_header:
        return LinkRegion.header
    return LinkRegion.nav if in_nav else LinkRegion.body


def extract_links(tree: LexborHTMLParser, base_url: str) -> list[PageLink]:
    links: list[PageLink] = []
    seen: set[tuple[str, str]] = set()
    for node in tree.css("a[href]"):
        url = _absolute(node.attributes.get("href") or "", base_url)
        if url is None:
            continue
        label = _anchor_label(node)
        if (url, label) in seen:
            continue
        seen.add((url, label))
        links.append(PageLink(url=url, anchor=label, region=_region(node)))
        if len(links) >= MAX_LINKS:
            break
    return links


class NavExtractor:
    """site taxonomy (label, url, depth) from navigation containers (plan 03 §5.4.4)."""

    def extract(self, tree: LexborHTMLParser, base_url: str) -> list[NavItem]:
        items: dict[str, NavItem] = {}
        for container in tree.css(NAV_CONTAINERS):
            self._collect(container, base_url, items, require_list=False)
        for container in tree.css("header, footer"):
            self._collect(container, base_url, items, require_list=True)
        ordered = sorted(items.values(), key=lambda item: item.depth)
        return ordered[:MAX_NAV_ITEMS]

    def _collect(
        self, container: LexborNode, base_url: str, items: dict[str, NavItem], *, require_list: bool
    ) -> None:
        for anchor in container.css("a[href]"):
            url = _absolute(anchor.attributes.get("href") or "", base_url)
            label = _anchor_label(anchor)
            if url is None or url.startswith(NON_WEB_SCHEMES) or not label:
                continue
            depth = self._list_depth(anchor, container)
            if require_list and depth == 0:
                continue
            depth = max(depth, 1)
            current = items.get(url)
            if current is None or depth < current.depth:
                items[url] = NavItem(label=label, url=url, depth=depth)

    def _list_depth(self, node: LexborNode, container: LexborNode) -> int:
        depth = 0
        parent = node.parent
        while parent is not None and parent.mem_id != container.mem_id:
            if parent.tag in {"ul", "ol"}:
                depth += 1
            parent = parent.parent
        return depth + (1 if container.tag in {"ul", "ol"} else 0)


def youtube_embeds(tree: LexborHTMLParser, base_url: str, meta: MetaInfo) -> list[str]:
    found: list[str] = []
    for node in tree.css("iframe"):
        src = node.attributes.get("src") or node.attributes.get("data-src") or ""
        url = _absolute(src, base_url) if src else None
        if url and is_youtube_url(url):
            found.append(url)
    # lazy players and video modals keep the url in a data attribute until clicked
    for attribute in VIDEO_DATA_ATTRIBUTES:
        for node in tree.css(f"[{attribute}]"):
            value = node.attributes.get(attribute) or ""
            url = _absolute(value, base_url) if value else None
            if url and is_youtube_url(url):
                found.append(url)
    video = meta.og.get("og:video") or meta.og.get("og:video:url")
    if video and is_youtube_url(video):
        found.append(video)
    return list(dict.fromkeys(found))


def is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in YOUTUBE_HOSTS)


def same_as_urls(json_ld: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for entity in json_ld:
        same_as = entity.get("sameAs")
        values = [same_as] if isinstance(same_as, str) else same_as if isinstance(same_as, list) else []
        urls.extend(value for value in values if isinstance(value, str))
    return urls


def extract_forms(tree: LexborHTMLParser, base_url: str) -> list[FormInfo]:
    forms: list[FormInfo] = []
    for form in tree.css("form"):
        action = _absolute(form.attributes.get("action") or "", base_url) or base_url
        fields: list[str] = []
        for control in form.css("input, select, textarea"):
            attributes = control.attributes
            if (attributes.get("type") or "").lower() in {"hidden", "submit", "button"}:
                continue
            for key in ("name", "type", "id", "placeholder"):
                value = (attributes.get(key) or "").strip().lower()
                if value:
                    fields.append(value)
        button = form.css_first("button, input[type=submit]")
        label = ""
        if button is not None:
            label = normalize_text(button.text(deep=True) or button.attributes.get("value") or "")
        forms.append(FormInfo(action=action, fields=tuple(fields), submit_label=label[:MAX_LABEL_CHARS]))
    return forms


def _prose_strings(value: Any, key: str | None = None) -> Iterator[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith(("http", "/", "{", "<")):
            return
        if (key or "").lower() in CONTENT_KEYS or (len(text) >= 20 and _PROSE_WORDS.search(text)):
            yield text
    elif isinstance(value, dict):
        for child_key, item in value.items():
            yield from _prose_strings(item, child_key if isinstance(child_key, str) else None)
    elif isinstance(value, list):
        for item in value:
            yield from _prose_strings(item, key)


def embedded_app_state_text(html: str) -> str | None:
    """prose strings from next.js / nuxt hydration payloads, which often removes the need to render."""
    tree = LexborHTMLParser(html)
    strings: list[str] = []
    for selector in ("script#__NEXT_DATA__", "script#__NUXT_DATA__"):
        node = tree.css_first(selector)
        if node is not None:
            strings.extend(_prose_strings(_parse_lenient(node.text(deep=True) or "")))
    for node in tree.css("script"):
        body = node.text(deep=True) or ""
        if "window.__NUXT__" in body:
            for match in _QUOTED.finditer(body):
                try:
                    strings.extend(_prose_strings(json.loads(f'"{match.group(1)}"')))
                except json.JSONDecodeError:
                    continue
    unique = list(dict.fromkeys(normalize_text(text) for text in strings))
    return "\n".join(text for text in unique if text) or None
