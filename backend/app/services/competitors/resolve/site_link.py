from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from app.domain.web import FetchOutcome, FetchResult
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import registrable_domain
from app.services.competitors.model import CandidateEntity, LeadRef, LinkEvidence
from app.services.competitors.providers import fetch_page
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ResolverCost, ResolverDeps
from app.services.competitors.resolve.youtube_url import YouTubeRef, parse_youtube_url

CHANNEL_KINDS = frozenset({"channel_id", "handle", "username", "custom", "playlist"})
DEAD_OUTCOMES = frozenset({FetchOutcome.not_found, FetchOutcome.error, FetchOutcome.timeout})


def same_as_urls(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sameAs":
                values = value if isinstance(value, list) else [value]
                yield from (item for item in values if isinstance(item, str))
            else:
                yield from same_as_urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from same_as_urls(item)


def page_links(page: FetchResult) -> tuple[list[str], list[str]]:
    """anchors plus outbound links, and embed sources, all absolute."""
    anchors = list(page.outbound_links)
    embeds: list[str] = []
    if page.html:
        tree = LexborHTMLParser(page.html)
        anchors.extend(node.attributes.get("href") or "" for node in tree.css("a[href]"))
        embeds.extend(node.attributes.get("src") or "" for node in tree.css("iframe[src]"))
        embeds.extend(node.attributes.get("data-src") or "" for node in tree.css("iframe[data-src]"))
    base = page.final_url or page.requested_url
    absolute = lambda links: [urljoin(base, link) for link in links if link]  # noqa: E731
    return absolute(anchors), absolute(embeds)


def refs_from_page(page: FetchResult) -> list[LeadRef]:
    anchors, embeds = page_links(page)
    refs: list[LeadRef] = []
    for url in dict.fromkeys(same_as_urls(page.json_ld)):
        parsed = parse_youtube_url(url)
        if isinstance(parsed, YouTubeRef) and parsed.kind in CHANNEL_KINDS:
            detail = f"{page.final_url} lists {url} in its JSON-LD sameAs"
            refs.append(
                _ref(
                    parsed,
                    "jsonld_sameas",
                    LinkEvidence(name="jsonld_sameas", detail=detail, source_url=page.final_url),
                )
            )
    for url in dict.fromkeys(anchors):
        parsed = parse_youtube_url(url)
        if not isinstance(parsed, YouTubeRef):
            continue
        if parsed.kind in CHANNEL_KINDS:
            detail = f"{page.final_url} links {url}"
            refs.append(
                _ref(
                    parsed,
                    "site_link",
                    LinkEvidence(name="site_links_channel", detail=detail, source_url=page.final_url),
                )
            )
        elif parsed.kind == "video":
            refs.append(
                _ref(
                    parsed,
                    "embed_vote",
                    LinkEvidence(name="embed_vote", detail=url, source_url=page.final_url),
                )
            )
    for url in dict.fromkeys(embeds):
        parsed = parse_youtube_url(url)
        if isinstance(parsed, YouTubeRef) and parsed.kind == "video":
            refs.append(
                _ref(
                    parsed,
                    "embed_vote",
                    LinkEvidence(name="embed_vote", detail=url, source_url=page.final_url),
                )
            )
    return refs


def _ref(parsed: YouTubeRef, method: str, evidence: LinkEvidence) -> LeadRef:
    return LeadRef(kind=parsed.kind, value=parsed.value, method=method, evidence=[evidence])


@channel_resolver_registry.register("site_link")
class OfficialSiteLinkResolver:
    """reads the brand's own pages for channel links, json-ld sameAs and embedded videos; never bypasses blocks."""

    name = "site_link"
    order = 20
    cost = ResolverCost()

    def __init__(self, deps: ResolverDeps) -> None:
        self.crawler = deps.crawler

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        return self.crawler is not None and entity.official_domain is not None and not entity.channel_hints

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        domain = entity.official_domain
        refs: list[LeadRef] = []
        for index, path in enumerate(["/", *ctx.settings.site_extra_paths]):
            page = await fetch_page(ctx, self.crawler, f"https://{domain}{path}")
            if index == 0 and not self._usable_home(entity, page):
                return []
            if page is None or page.outcome is not FetchOutcome.ok:
                continue
            if registrable_domain(page.final_url) not in {domain, None}:
                entity.flags.add(f"redirects_to:{registrable_domain(page.final_url)}")
            refs.extend(refs_from_page(page))
            if any(ref.kind in CHANNEL_KINDS for ref in refs):
                break
        return refs

    @staticmethod
    def _usable_home(entity: CandidateEntity, page: FetchResult | None) -> bool:
        if page is not None and page.blocked:
            entity.flags.add("site_blocked")
            return False
        if page is None or page.outcome in DEAD_OUTCOMES:
            # an llm-guessed domain that does not answer is discarded (edge case 17)
            if "domain_unverified" in entity.flags:
                entity.flags.add("domain_dropped")
                entity.official_domain = None
            else:
                entity.flags.add("site_unreachable")
            return False
        return page.outcome is FetchOutcome.ok
