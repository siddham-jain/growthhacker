import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from selectolax.lexbor import LexborHTMLParser

from app.domain.web import CrawledPage, SiteRef, SourceKind
from app.services.website_intel.extract.cta import CtaExtractor, FormInfo, markdown_links
from app.services.website_intel.extract.fingerprint import content_hash, simhash64
from app.services.website_intel.extract.injection import InjectionScanner
from app.services.website_intel.extract.language import LanguageDetector
from app.services.website_intel.extract.main_text import MIN_MAIN_TEXT_CHARS, MainTextExtractor
from app.services.website_intel.extract.prices import PriceSignalExtractor
from app.services.website_intel.extract.sanitizer import DomSanitizer, drop_code_lines, normalize_text
from app.services.website_intel.extract.structured import (
    MetaInfo,
    NavExtractor,
    embedded_app_state_text,
    extract_forms,
    extract_links,
    extract_meta,
    is_youtube_url,
    json_ld_blocks,
    published_at,
    same_as_urls,
    youtube_embeds,
)
from app.services.website_intel.inputs.urls import (
    InvalidUrlError,
    host_of,
    in_scope,
    normalize_url,
    registrable_domain,
)
from app.services.website_intel.models import (
    ExtractedPage,
    Hreflang,
    LinkRegion,
    NavItem,
    PageDetails,
    PageLink,
)

DOMAIN_TEXT_CAP = 60_000
MAX_INTERNAL_LINKS = 300
MAX_HEADINGS = 100
MAX_HEADING_CHARS = 200
MIN_NAV_RUN = 3
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(\s*<?[^)\s>]+>?(?:\s+\"[^\"]*\")?\s*\)")
_LINK_ONLY_LINE = re.compile(
    r"^(?P<indent>\s*)(?:[*+-]|\d+\.)?\s*\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)[^)]*\)\s*$"
)
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _web_url(url: str, base: str | None = None) -> str | None:
    try:
        return normalize_url(url, base=base)
    except InvalidUrlError:
        return None


class PageExtractor:
    """turns html, markdown or plain text from any acquisition tier into a page plus its signals."""

    def __init__(self, *, stored_text_char_cap: int = 60_000) -> None:
        self._text_cap = min(stored_text_char_cap, DOMAIN_TEXT_CAP)
        self._sanitizer = DomSanitizer()
        self._main_text = MainTextExtractor()
        self._nav = NavExtractor()
        self._ctas = CtaExtractor()
        self._prices = PriceSignalExtractor()
        self._language = LanguageDetector()
        self._injection = InjectionScanner()

    def from_html(
        self,
        *,
        html: str,
        requested_url: str,
        final_url: str,
        site: SiteRef | None,
        source_kind: SourceKind,
        status_code: int | None,
        content_type: str | None,
        fetched_at: datetime,
        snapshot_at: datetime | None = None,
        country_hint: str | None = None,
    ) -> ExtractedPage:
        base = _web_url(final_url) or final_url
        tree = LexborHTMLParser(html)
        json_ld = json_ld_blocks(tree)
        meta = extract_meta(tree, base)
        links = extract_links(tree, base)
        nav = self._nav.extract(tree, base)
        embeds = youtube_embeds(tree, base, meta)
        forms = extract_forms(tree, base)
        self._sanitizer.strip(tree)
        h1, headings = self._headings(tree)
        text = self._main_text.extract(tree, url=base)
        if len(text) < MIN_MAIN_TEXT_CHARS and (state_text := embedded_app_state_text(html)):
            text = f"{text}\n{state_text}".strip()
        canonical = meta.canonical if meta.canonical and self._internal(meta.canonical, site, base) else base
        declared = meta.lang or self._self_hreflang(meta.hreflang, base)
        return self._assemble(
            text=text,
            requested_url=requested_url,
            final_url=base,
            canonical_url=canonical,
            site=site,
            source_kind=source_kind,
            status_code=status_code,
            content_type=content_type,
            title=meta.title or meta.og.get("og:title"),
            meta=meta,
            headings=headings,
            h1=h1,
            json_ld=json_ld,
            links=links,
            nav=nav,
            forms=forms,
            youtube=same_as_urls(json_ld),
            embeds=embeds,
            declared_lang=declared,
            published=published_at(None, json_ld) if meta.published_at is None else meta.published_at,
            fetched_at=fetched_at,
            snapshot_at=snapshot_at,
            country_hint=country_hint,
        )

    def from_markdown(
        self,
        *,
        markdown: str,
        requested_url: str,
        final_url: str,
        title: str | None,
        site: SiteRef | None,
        source_kind: SourceKind,
        fetched_at: datetime,
        snapshot_at: datetime | None = None,
        country_hint: str | None = None,
    ) -> ExtractedPage:
        base = _web_url(final_url) or final_url
        body, nav = self._split_nav_runs(_MARKDOWN_IMAGE.sub("", markdown), base)
        nav_urls = {item.url for item in nav}
        links = []
        for link in markdown_links(markdown):
            url = (
                link.url
                if link.url.lower().startswith(("tel:", "mailto:", "whatsapp:"))
                else _web_url(link.url, base)
            )
            if url is not None:
                region = LinkRegion.nav if url in nav_urls else LinkRegion.body
                links.append(PageLink(url=url, anchor=link.anchor, region=region))
        text = normalize_text(_MARKDOWN_LINK.sub(lambda match: match.group(1), body))
        headings = [
            match.group(2).strip()[:MAX_HEADING_CHARS]
            for match in map(_HEADING_LINE.match, text.split("\n"))
            if match
        ]
        h1 = next(
            (
                match.group(2).strip()
                for match in map(_HEADING_LINE.match, text.split("\n"))
                if match and match.group(1) == "#"
            ),
            None,
        )
        return self._assemble(
            text=text,
            requested_url=requested_url,
            final_url=base,
            canonical_url=base,
            site=site,
            source_kind=source_kind,
            status_code=None,
            content_type="text/markdown",
            title=title or h1,
            meta=MetaInfo(),
            headings=headings[:MAX_HEADINGS],
            h1=h1,
            json_ld=[],
            links=links,
            nav=nav,
            forms=[],
            youtube=[],
            declared_lang=None,
            published=None,
            fetched_at=fetched_at,
            snapshot_at=snapshot_at,
            country_hint=country_hint,
        )

    def from_text(
        self,
        *,
        text: str,
        url: str,
        title: str | None,
        source_kind: SourceKind,
        fetched_at: datetime,
        snapshot_at: datetime | None = None,
        links: Sequence[PageLink] = (),
        lang: str | None = None,
        country_hint: str | None = None,
    ) -> ExtractedPage:
        base = _web_url(url) or url
        return self._assemble(
            text=normalize_text(text),
            requested_url=url,
            final_url=base,
            canonical_url=base,
            site=None,
            source_kind=source_kind,
            status_code=None,
            content_type="text/plain",
            title=title,
            meta=MetaInfo(),
            headings=[],
            json_ld=[],
            links=list(links),
            nav=[],
            forms=[],
            youtube=[],
            declared_lang=lang,
            published=None,
            fetched_at=fetched_at,
            snapshot_at=snapshot_at,
            country_hint=country_hint,
        )

    def _assemble(
        self,
        *,
        text: str,
        requested_url: str,
        final_url: str,
        canonical_url: str,
        site: SiteRef | None,
        source_kind: SourceKind,
        status_code: int | None,
        content_type: str | None,
        title: str | None,
        meta: MetaInfo,
        headings: list[str],
        json_ld: list[dict[str, Any]],
        links: list[PageLink],
        nav: list[NavItem],
        forms: list[FormInfo],
        youtube: list[str],
        declared_lang: str | None,
        published: datetime | None,
        fetched_at: datetime,
        snapshot_at: datetime | None,
        country_hint: str | None,
        h1: str | None = None,
        embeds: Sequence[str] = (),
    ) -> ExtractedPage:
        scan = self._injection.scan(drop_code_lines(text))
        clean = scan.text
        prices = self._prices.extract(clean, page_url=final_url, json_ld=json_ld)
        ctas = self._ctas.extract(links, page_url=final_url, forms=forms, country_hint=country_hint)
        truncated = len(clean) > self._text_cap
        stored = clean[: self._text_cap]
        digest = content_hash(stored)
        page_id = uuid5(NAMESPACE_URL, f"{source_kind}:{canonical_url}:{digest}")
        youtube_refs = list(dict.fromkeys([*(link.url for link in links), *embeds, *youtube]))
        page = CrawledPage(
            page_id=page_id,
            url=_web_url(requested_url) or requested_url,
            canonical_url=canonical_url,
            final_url=final_url,
            source_kind=source_kind,
            status_code=status_code,
            content_type=content_type,
            lang=self._language.detect(clean, declared_lang),
            title=title,
            meta_description=meta.description,
            h1=h1,
            headings=headings,
            text=stored,
            content_hash=digest,
            json_ld=json_ld,
            links_internal=self._internal_links(links, site, final_url),
            youtube_refs=[url for url in youtube_refs if is_youtube_url(url)],
            injection_flags=scan.flags,
            truncated=truncated,
            fetched_at=fetched_at,
            snapshot_at=snapshot_at,
        )
        details = PageDetails(
            page_id=page_id,
            links=links,
            nav=nav,
            ctas=ctas,
            prices=prices,
            hreflang=meta.hreflang,
            meta=meta.flat(),
            youtube_embeds=list(embeds),
            published_at=published,
            simhash=simhash64(stored),
            pdf_links=[link for link in links if urlsplit(link.url).path.lower().endswith(".pdf")],
        )
        return ExtractedPage(page=page, details=details)

    def _headings(self, tree: LexborHTMLParser) -> tuple[str | None, list[str]]:
        h1: str | None = None
        headings: list[str] = []
        for node in tree.css("h1, h2, h3"):
            text = normalize_text(node.text(deep=True, separator=" ") or "")[:MAX_HEADING_CHARS]
            if text:
                headings.append(text)
                if h1 is None and node.tag == "h1":
                    h1 = text
        return h1, headings[:MAX_HEADINGS]

    def _internal(self, url: str, site: SiteRef | None, base: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        if site is not None:
            return in_scope(url, site.scope_hosts)
        domain = registrable_domain(host_of(base))
        return domain is not None and registrable_domain(host_of(url)) == domain

    def _internal_links(self, links: list[PageLink], site: SiteRef | None, base: str) -> list[str]:
        internal = dict.fromkeys(link.url for link in links if self._internal(link.url, site, base))
        return list(internal)[:MAX_INTERNAL_LINKS]

    def _self_hreflang(self, alternates: list[Hreflang], base: str) -> str | None:
        return next((item.lang for item in alternates if item.url == base and item.lang != "x-default"), None)

    def _split_nav_runs(self, markdown: str, base: str) -> tuple[str, list[NavItem]]:
        """link-only list runs (mega menus in reader output) become nav items and leave the text."""
        lines = markdown.split("\n")
        kept: list[str] = []
        nav: list[NavItem] = []
        run: list[tuple[str, re.Match[str]]] = []

        def flush() -> None:
            if len(run) >= MIN_NAV_RUN:
                for _, match in run:
                    url = _web_url(match.group("url"), base)
                    if url is not None:
                        depth = len(match.group("indent").expandtabs(4)) // 2 + 1
                        nav.append(NavItem(label=match.group("label").strip(), url=url, depth=depth))
            else:
                kept.extend(line for line, _ in run)
            run.clear()

        for line in lines:
            match = _LINK_ONLY_LINE.match(line)
            if match is not None:
                run.append((line, match))
            elif not line.strip() and run:
                continue
            else:
                flush()
                kept.append(line)
        flush()
        return "\n".join(kept), nav
