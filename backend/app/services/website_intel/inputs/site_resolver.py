import re
from dataclasses import dataclass, field
from typing import Literal

from app.domain.web import SiteRef, UserAgentMode
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.fetching import RawFetcher, RawResponse
from app.services.website_intel.inputs.ssrf import DnsLookupError, UnsafeTargetError
from app.services.website_intel.inputs.urls import (
    BAD_ADDRESS,
    InvalidUrlError,
    host_of,
    is_ip_literal,
    normalize_url,
    origin_of,
    path_of,
    registrable_domain,
    www_twin,
)
from app.services.website_intel.models import PageVerdict

SOCIAL_HOSTS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "linktr.ee",
    "x.com",
    "twitter.com",
    "wa.me",
    "whatsapp.com",
    "tiktok.com",
    "beacons.ai",
    "bio.link",
    "youtube.com",
)
MARKETPLACE_HOSTS = ("practo.com", "amazon.in", "amazon.com", "etsy.com", "justdial.com", "flipkart.com")
PARKED_TEXT = re.compile(
    r"domain (?:is|may be) for sale|buy this domain|parked free|this domain is parked|domain parking",
    re.IGNORECASE,
)
UNDER_CONSTRUCTION = re.compile(r"coming soon|under construction|site is under maintenance", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_HREFS = re.compile(r"href=[\"'](https?://[^\"']+)", re.IGNORECASE)

SiteStatus = Literal[
    "ok",
    "blocked",
    "social",
    "marketplace",
    "parked",
    "moved",
    "unreachable",
    "unsafe",
    "invalid",
    "maintenance",
]
MESSAGES: dict[str, str] = {
    "social": "Your web address points to a social profile. We'll build your profile from your "
    "YouTube channel and a few questions.",
    "marketplace": "Your link is a marketplace listing; profile will be limited.",
    "parked": "This domain looks parked or for sale, not an active business site.",
    "unreachable": "Your website isn't responding right now. We'll retry automatically; you can continue "
    "setup meanwhile.",
    "maintenance": "Your site looks like it's under construction.",
}


@dataclass
class SiteResolution:
    status: SiteStatus
    site: SiteRef | None
    message: str | None = None
    moved_to: str | None = None
    home: RawResponse | None = None
    notes: list[str] = field(default_factory=list)


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def looks_parked(html: str, host_domain: str) -> bool:
    text = " ".join(_TAGS.sub(" ", html).split())
    if PARKED_TEXT.search(text):
        return True
    links = _HREFS.findall(html)
    off_site = [link for link in links if registrable_domain(host_of(link)) != host_domain]
    return len(text) < 300 and bool(links) and len(off_site) / len(links) > 0.5


class SiteResolver:
    """normalises the typed address, checks reachability and fixes the crawl scope (plan 03 §5.1)."""

    def __init__(self, fetcher: RawFetcher) -> None:
        self._fetcher = fetcher

    async def resolve(self, raw_url: str, ctx: FetchContext, *, scope_mode: str = "domain") -> SiteResolution:
        try:
            url = normalize_url(raw_url)
        except InvalidUrlError as error:
            return SiteResolution("invalid", None, error.user_message)
        host = host_of(url)
        domain = registrable_domain(host)
        if is_ip_literal(host) or domain is None:
            return SiteResolution("invalid", None, BAD_ADDRESS)
        if _host_matches(host, SOCIAL_HOSTS):
            return SiteResolution("social", None, MESSAGES["social"])
        try:
            home = await self._fetcher.fetch_raw(origin_of(url) + "/", ctx)
            if home.status_code in {401, 403}:
                home = await self._fetcher.fetch_raw(
                    origin_of(url) + "/", ctx.model_copy(update={"ua_mode": UserAgentMode.compat})
                )
        except UnsafeTargetError as error:
            return SiteResolution("unsafe", None, error.user_message)
        except DnsLookupError as error:
            return SiteResolution("unreachable", None, error.user_message)
        final_host = host_of(home.final_url) or host
        final_domain = registrable_domain(final_host) or domain
        focus = path_of(url) if scope_mode == "subpath" and path_of(url) != "/" else None
        site = SiteRef(
            input_url=raw_url,
            canonical_origin=origin_of(home.final_url) if home.status_code else origin_of(url),
            registrable_domain=final_domain,
            scope_hosts=sorted({final_host, www_twin(final_host)}),
            focus_path=focus,
            redirect_chain=home.redirect_chain,
        )
        if _host_matches(final_host, SOCIAL_HOSTS):
            return SiteResolution("social", None, MESSAGES["social"], home=home)
        if _host_matches(final_host, MARKETPLACE_HOSTS):
            return SiteResolution("marketplace", site, MESSAGES["marketplace"], home=home)
        if final_domain != domain:
            return SiteResolution(
                "moved",
                site,
                f"{domain} now redirects to {final_domain}. Use that instead?",
                moved_to=final_domain,
                home=home,
            )
        if home.status_code is None or home.verdict is PageVerdict.server_error:
            return SiteResolution("unreachable", site, MESSAGES["unreachable"], home=home)
        if home.verdict.is_block:
            return SiteResolution("blocked", site, None, home=home)
        html = home.decoded() if home.body else ""
        if looks_parked(html, final_domain):
            return SiteResolution("parked", None, MESSAGES["parked"], home=home)
        if UNDER_CONSTRUCTION.search(html[:20_000]) and len(_TAGS.sub(" ", html).split()) < 120:
            return SiteResolution("maintenance", site, MESSAGES["maintenance"], home=home)
        return SiteResolution("ok", site, home=home)
