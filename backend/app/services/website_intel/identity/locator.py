from collections.abc import Sequence
from typing import Literal, Protocol
from uuid import UUID

from app.adapters.search.errors import WebSearchError
from app.domain.runs import ChannelResolvedPayload
from app.domain.web import SiteRef, SourceKind
from app.domain.youtube import (
    ChannelRecord,
    ChannelResolution,
    OwnershipLabel,
    OwnershipVerdict,
    QuotaPriority,
)
from app.ports.clock import Clock
from app.ports.context import BusinessLookup, LocatedBusiness
from app.ports.crawler import FetchContext
from app.ports.progress import ProgressReporter
from app.ports.search import WebSearch, WebSearchHit, WebSearchQuery
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.competitors.classify.geo import TAVILY_COUNTRY_NAMES
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.identity.channel_links import ChannelLinkCollector, SiteChannelSignals
from app.services.website_intel.identity.knowledge import KbEntity, KnowledgeBase, pick_entity
from app.services.website_intel.identity.related_brands import name_tokens
from app.services.website_intel.identity.site_from_channel import PLATFORM_DOMAINS, SiteFromChannel
from app.services.website_intel.inputs.site_resolver import SiteResolution, SiteResolver
from app.services.website_intel.inputs.urls import host_of, registrable_domain
from app.services.website_intel.models import ExtractedPage, PageVerdict
from app.services.youtube.input_parser import InputParser
from app.services.youtube.ownership import BusinessHint

MAX_CHANNEL_LOOKUPS = 2
RESOLVED_VIA = {
    "id": "id",
    "handle": "forHandle",
    "username": "forUsername",
    "video": "video",
    "playlist": "id",
    "custom_url_search": "forHandle",
}


class ChannelResolverPort(Protocol):
    async def resolve(
        self, raw: str, ctx: RequestContext, *, hint: BusinessHint | None = None
    ) -> ChannelResolution: ...


def in_market(domain: str, hit: WebSearchHit, country: str | None, country_name: str | None) -> bool:
    if not country:
        return True
    tld = country.lower()
    if domain.endswith(f".{tld}") or f".{tld}." in domain:
        return True
    text = f"{hit.title} {hit.snippet}".lower()
    return bool(country_name) and country_name in text


class LocatedBusinessDetail(LocatedBusiness):
    # the port result plus what later stages need; fields the shared contract has no slot for
    site_status: str | None = None
    site_message: str | None = None
    channel_title: str | None = None
    channel_handle: str | None = None
    channel_resolved_via: Literal["id", "forHandle", "forUsername", "video", "user"] | None = None
    ownership: OwnershipVerdict | None = None
    knowledge_entity: KbEntity | None = None
    channel_candidates: list[str] = []


class DefaultBusinessLocator:
    """resolves the official site and channel from a website, a channel or only a name (plan 03 §14)."""

    def __init__(
        self,
        *,
        site_resolver: SiteResolver,
        extractor: PageExtractor,
        knowledge: KnowledgeBase,
        channel_resolver: ChannelResolverPort | None,
        gateway: YouTubeGateway | None,
        site_from_channel: SiteFromChannel,
        clock: Clock,
        web_search: WebSearch | None = None,
        collector: ChannelLinkCollector | None = None,
        parser: InputParser | None = None,
    ) -> None:
        self._sites = site_resolver
        self._extractor = extractor
        self._knowledge = knowledge
        self._resolver = channel_resolver
        self._gateway = gateway
        self._site_from_channel = site_from_channel
        self._clock = clock
        self._search = web_search
        self._collector = collector or ChannelLinkCollector()
        self._parser = parser or InputParser()

    async def locate(
        self,
        lookup: BusinessLookup,
        reporter: ProgressReporter,
        *,
        client_id: UUID | None = None,
        run_id: UUID | None = None,
        scope_mode: str = "domain",
    ) -> LocatedBusinessDetail:
        fetch_ctx = FetchContext(client_id=client_id, run_id=run_id)
        yt_ctx = RequestContext(client_id=client_id, run_id=run_id, priority=QuotaPriority.interactive)
        notes: list[str] = []
        name = lookup.business_name
        entity: KbEntity | None = None
        site_basis = None
        channel: ChannelRecord | None = None
        resolution_out: ChannelResolution | None = None
        site_url = lookup.website_url
        if site_url:
            site_basis = "user_input"
        if lookup.channel_ref and not site_url and self._resolver is not None:
            resolution_out = await self._resolver.resolve(lookup.channel_ref, yt_ctx)
            channel = resolution_out.channel
            if channel is not None:
                candidate = await self._site_from_channel.candidate(channel.description, [], fetch_ctx)
                if candidate is not None:
                    site_url, site_basis = candidate.url, "channel_link"
                name = name or channel.title
        if not site_url and name:
            await reporter.step("identity.lookup", f"Looking up {name}")
            entity = await self._knowledge_entity(name, None)
            if entity is not None and entity.websites:
                site_url, site_basis = entity.websites[0], "knowledge_base"
            else:
                site_url = await self._search_official_site(name, notes, lookup.country_hint)
                site_basis = "web_search" if site_url else None
        resolution: SiteResolution | None = None
        site: SiteRef | None = None
        if site_url:
            resolution = await self._sites.resolve(site_url, fetch_ctx, scope_mode=scope_mode)
            site = resolution.site
            if resolution.message:
                notes.append(resolution.message)
            await reporter.step("identity.site", f"Website: {site.registrable_domain if site else site_url}")
        if site is not None and entity is None and name:
            entity = await self._knowledge_entity(name, site.registrable_domain)
        name = name or (entity.label if entity else None)
        home = self._home_page(resolution, site)
        signals = self._collector.collect([home]) if home is not None else None
        confirmed = site is not None and (
            site_basis == "user_input" or (entity is not None and site.registrable_domain in entity.domains)
        )
        hint = await self._hint(signals, name, site, lookup, yt_ctx)
        hint = hint.model_copy(update={"site_identity_confirmed": confirmed})
        channel_basis = None
        method: str | None = None
        verdict: OwnershipVerdict | None = None
        candidates: list[str] = []
        if self._resolver is None:
            notes.append("YouTube lookups are unavailable; the channel was not resolved.")
        elif lookup.channel_ref:
            resolution_out = await self._resolver.resolve(lookup.channel_ref, yt_ctx, hint=hint)
            channel, verdict, channel_basis = resolution_out.channel, resolution_out.ownership, "user_input"
            method = resolution_out.method
            if channel is None and resolution_out.message:
                notes.append(resolution_out.message)
        else:
            channel, verdict, channel_basis, candidates, method = await self._discover_channel(
                signals, hint, name, yt_ctx, notes
            )
        if channel is not None:
            await reporter.insight(
                f"Found YouTube channel {channel.title}",
                ChannelResolvedPayload(
                    channel_id=channel.channel_id,
                    title=channel.title,
                    handle=channel.handle,
                    thumbnail_url=channel.thumbnail_url,
                    subscriber_count=channel.subscriber_count,
                    verified_by="website_link"
                    if verdict and verdict.label is OwnershipLabel.verified
                    else ("channel_link" if channel_basis == "user_input" else "manual"),
                ),
            )
        verified = verdict is not None and verdict.label is OwnershipLabel.verified
        return LocatedBusinessDetail(
            business_name=name,
            site=site,
            site_basis=site_basis if site is not None else None,  # type: ignore[arg-type]
            channel_id=channel.channel_id if channel else None,
            channel_basis=channel_basis if channel else None,  # type: ignore[arg-type]
            needs_user_confirmation=(site is not None and site_basis != "user_input")
            or (channel is not None and not verified)
            or site is None,
            notes=notes,
            site_status=resolution.status if resolution else None,
            site_message=resolution.message if resolution else None,
            channel_title=channel.title if channel else None,
            channel_handle=channel.handle if channel else None,
            channel_resolved_via=RESOLVED_VIA.get(method or "") if channel else None,  # type: ignore[arg-type]
            ownership=verdict,
            knowledge_entity=entity,
            channel_candidates=candidates,
        )

    async def _knowledge_entity(self, name: str, domain: str | None) -> KbEntity | None:
        candidates = await self._knowledge.search(name)
        return pick_entity(candidates, name=name, domain=domain)

    async def _search_official_site(
        self, name: str, notes: list[str], country: str | None = None
    ) -> str | None:
        if self._search is None:
            return None
        country_name = TAVILY_COUNTRY_NAMES.get(country or "")
        query = f'"{name}" official website' + (f" {country_name}" if country_name else "")
        try:
            response = await self._search.search(
                WebSearchQuery(query=query, max_results=5, country=country_name)
            )
        except WebSearchError as error:
            notes.append(f"web search unavailable: {error.kind}")
            return None
        tokens = name_tokens(name) or [name.lower().replace(" ", "")]
        matches = []
        for rank, hit in enumerate(response.hits):
            domain = registrable_domain(host_of(hit.url))
            if domain and domain not in PLATFORM_DOMAINS and tokens[0] in domain.replace("-", ""):
                matches.append((not in_market(domain, hit, country, country_name), rank, host_of(hit.url)))
        # a same-name business abroad must not win over the one in the client's market
        return f"https://{min(matches)[2]}/" if matches else None

    def _home_page(self, resolution: SiteResolution | None, site: SiteRef | None) -> ExtractedPage | None:
        if resolution is None or site is None or resolution.home is None:
            return None
        home = resolution.home
        if home.verdict is not PageVerdict.ok or not home.body:
            return None
        return self._extractor.from_html(
            html=home.decoded(),
            requested_url=home.requested_url,
            final_url=home.final_url,
            site=site,
            source_kind=SourceKind.site_direct,
            status_code=home.status_code,
            content_type=home.content_type,
            fetched_at=self._clock.now(),
        )

    async def _hint(
        self,
        signals: SiteChannelSignals | None,
        name: str | None,
        site: SiteRef | None,
        lookup: BusinessLookup,
        ctx: RequestContext,
    ) -> BusinessHint:
        domain = site.registrable_domain if site else None
        if signals is None:
            return BusinessHint(name=name, website_domain=domain, country=lookup.country_hint)
        hint = signals.hint(name=name, domain=domain, country=lookup.country_hint)
        if signals.embedded_video_ids and self._gateway is not None:
            batch = await self._gateway.videos_by_id(signals.embedded_video_ids[:50], ctx)
            hint = hint.model_copy(update={"embed_channel_ids": [video.channel_id for video in batch.found]})
        return hint

    async def _discover_channel(
        self,
        signals: SiteChannelSignals | None,
        hint: BusinessHint,
        name: str | None,
        ctx: RequestContext,
        notes: list[str],
    ) -> tuple[ChannelRecord | None, OwnershipVerdict | None, str | None, list[str], str | None]:
        resolver = self._resolver
        tried: list[str] = []
        if resolver is None:
            return None, None, None, tried, None
        sources: list[tuple[str, Sequence[str]]] = []
        if signals is not None and signals.candidates:
            sources.append(("website_link", [candidate.urls[0] for candidate in signals.candidates]))
        if name:
            sources.append(("web_search", await self._search_channel_urls(name, notes)))
            handle = "".join(word for word in name.split() if word.isalnum())
            sources.append(("handle_lookup", [f"@{handle}"] if handle else []))
        best: tuple[ChannelRecord, OwnershipVerdict | None, str, str] | None = None
        for basis, urls in sources:
            for url in list(dict.fromkeys(urls))[:MAX_CHANNEL_LOOKUPS]:
                tried.append(url)
                outcome = await resolver.resolve(url, ctx, hint=hint)
                if outcome.channel is None:
                    continue
                label = outcome.ownership.label if outcome.ownership else OwnershipLabel.uncertain
                if label is OwnershipLabel.verified:
                    return outcome.channel, outcome.ownership, basis, tried, outcome.method
                if best is None and label is not OwnershipLabel.rejected:
                    best = (outcome.channel, outcome.ownership, basis, outcome.method)
            if best is not None and basis == "website_link":
                break
        if best is None:
            notes.append("Add your website so we can understand your services.")
            return None, None, None, tried, None
        return best[0], best[1], best[2], tried, best[3]

    async def _search_channel_urls(self, name: str, notes: list[str]) -> list[str]:
        if self._search is None:
            return []
        try:
            response = await self._search.search(
                WebSearchQuery(
                    query=f"{name} official YouTube channel", include_domains=["youtube.com"], max_results=5
                )
            )
        except WebSearchError as error:
            notes.append(f"web search unavailable: {error.kind}")
            return []
        urls = []
        for hit in response.hits:
            ref = self._parser.parse(hit.url)
            if ref.kind in {"channel_id", "handle", "username", "custom_url"}:
                urls.append(hit.url)
        return urls
