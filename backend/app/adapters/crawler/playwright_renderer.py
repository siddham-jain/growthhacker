import asyncio
import contextlib
from typing import Any

from playwright.async_api import Browser, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.domain.web import FetchOutcome, FetchResult, SourceKind, UserAgentMode
from app.ports.clock import Clock
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.inputs.ssrf import DnsLookupError, SsrfGuard, UnsafeTargetError

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font", "beacon", "ping", "websocket", "eventsource"})
ANALYTICS_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "hotjar.com",
    "clarity.ms",
    "segment.io",
    "mixpanel.com",
)
CONSENT_SELECTORS = (
    "#onetrust-banner-sdk",
    "#onetrust-consent-sdk",
    "#CybotCookiebotDialog",
    "#didomi-host",
    ".qc-cmp2-container",
    "#truste-consent-track",
    ".osano-cm-window",
    "[class*=cookie][class*=banner]",
)
MIN_BODY_TEXT = 400
TEXT_WAIT_MS = 6_000
NAVIGATION_TIMEOUT_MS = 20_000
_STRIP_CONSENT = "selectors => selectors.forEach(s => document.querySelectorAll(s).forEach(n => n.remove()))"
_TEXT_READY = f"() => (document.body && document.body.innerText.length >= {MIN_BODY_TEXT})"


class PlaywrightRenderer:
    """headless chromium render for javascript shells and browser-gated pages; never clicks or solves."""

    name = "render"

    def __init__(
        self,
        *,
        user_agents: UserAgents,
        clock: Clock,
        guard: SsrfGuard | None = None,
        classifier: BlockClassifier | None = None,
        max_pages: int = 2,
    ) -> None:
        self._agents = user_agents
        self._clock = clock
        self._guard = guard or SsrfGuard()
        self._classifier = classifier or BlockClassifier()
        self._slots = asyncio.Semaphore(max_pages)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._start_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser

    async def _route(self, route: Route) -> None:
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES or any(
            host in request.url for host in ANALYTICS_HOSTS
        ):
            await route.abort()
            return
        if request.url.startswith(("data:", "blob:")):
            await route.continue_()
            return
        try:
            await self._guard.check(request.url)
        except (UnsafeTargetError, DnsLookupError):
            await route.abort()
            return
        await route.continue_()

    async def render(self, url: str, ctx: FetchContext) -> FetchResult:
        await self._guard.check(url)
        async with self._slots:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                user_agent=self._agents.for_mode(UserAgentMode.compat),
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
            )
            try:
                return await self._render_in(context, url)
            finally:
                await context.close()

    async def _render_in(self, context: Any, url: str) -> FetchResult:
        page = await context.new_page()
        await page.route("**/*", self._route)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as error:
            outcome = FetchOutcome.timeout if "Timeout" in str(error) else FetchOutcome.error
            return self._result(url, url, outcome, None, None, {})
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_function(_TEXT_READY, timeout=TEXT_WAIT_MS)
        await page.evaluate(_STRIP_CONSENT, list(CONSENT_SELECTORS))
        html = await page.content()
        status = response.status if response is not None else None
        headers = await response.all_headers() if response is not None else {}
        verdict = self._classifier.classify(
            status_code=status, headers=headers, body=html, content_type=headers.get("content-type")
        )
        return self._result(url, page.url, verdict.fetch_outcome, status, html, headers, verdict.block_reason)

    def _result(
        self,
        requested_url: str,
        final_url: str,
        outcome: FetchOutcome,
        status: int | None,
        html: str | None,
        headers: dict[str, str],
        block_reason: Any = None,
    ) -> FetchResult:
        return FetchResult(
            requested_url=requested_url,
            final_url=final_url,
            source_kind=SourceKind.site_rendered,
            outcome=outcome,
            status_code=status,
            block_reason=block_reason,
            content_type=headers.get("content-type"),
            html=html if outcome is FetchOutcome.ok else None,
            fetched_at=self._clock.now(),
        )

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
