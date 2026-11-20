import asyncio
import contextlib
from typing import Any
from urllib.parse import urljoin

import httpx
from playwright.async_api import Browser, Playwright, Request, Route, WebSocketRoute, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.domain.web import FetchOutcome, FetchResult, SourceKind, UserAgentMode
from app.ports.clock import Clock
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.inputs.ssrf import (
    DnsLookupError,
    PinnedTransport,
    SsrfGuard,
    UnsafeTargetError,
    pinned,
)

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
MAX_REDIRECTS = 5
MAX_REQUESTS_PER_RENDER = 150
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# the browser has no network of its own: routed requests are fetched by the guarded client and anything
# that escapes routing (sockets, preconnects, worker fetches) meets a dead proxy and an empty resolver
NO_EGRESS_PROXY = {"server": "http://127.0.0.1:9", "bypass": "<-loopback>"}
LAUNCH_ARGS = (
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
    "--dns-prefetch-disable",
)
DROPPED_REQUEST_HEADERS = frozenset(
    {"host", "connection", "content-length", "accept-encoding", "transfer-encoding", "upgrade", "keep-alive"}
)
DROPPED_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"}
)
_NO_SOCKETS = (
    "for (const name of ['WebSocket', 'WebTransport', 'RTCPeerConnection', 'webkitRTCPeerConnection']) {"
    " try { Object.defineProperty(globalThis, name, { value: undefined, configurable: false }); } catch (e) {} }"
)
_STRIP_CONSENT = "selectors => selectors.forEach(s => document.querySelectorAll(s).forEach(n => n.remove()))"
_TEXT_READY = f"() => (document.body && document.body.innerText.length >= {MIN_BODY_TEXT})"


class _ResponseTooLarge(Exception):
    pass


def _is_main_navigation(request: Request) -> bool:
    try:
        return request.is_navigation_request() and request.frame.parent_frame is None
    except PlaywrightError:
        return False


class _GuardedRoutes:
    """one render's request broker: every hop is ssrf-checked, pinned and fetched outside the browser."""

    def __init__(self, client: httpx.AsyncClient, guard: SsrfGuard, max_bytes: int) -> None:
        self._client = client
        self._guard = guard
        self._max_bytes = max_bytes
        self._requests = 0
        self.violation: UnsafeTargetError | None = None
        self.redirect_to: str | None = None

    async def handle(self, route: Route) -> None:
        request = route.request
        if request.url.startswith(("data:", "blob:")):
            await route.continue_()
            return
        self._requests += 1
        if (
            self.violation is not None
            or self._requests > MAX_REQUESTS_PER_RENDER
            or request.resource_type in BLOCKED_RESOURCE_TYPES
            or any(host in request.url for host in ANALYTICS_HOSTS)
        ):
            await route.abort()
            return
        try:
            status, headers, body, location = await self._fetch(
                request, follow=not _is_main_navigation(request)
            )
        except UnsafeTargetError as error:
            self.violation = error
            await route.abort("blockedbyclient")
            return
        except (DnsLookupError, httpx.HTTPError, TimeoutError, _ResponseTooLarge):
            # an unresolvable cdn or a slow script is not an ssrf attempt; only that request fails
            await route.abort()
            return
        if location is not None:
            # the page itself navigates to the next hop, so its url and relative links stay right
            self.redirect_to = location
            await route.abort("aborted")
            return
        await route.fulfill(status=status, headers=headers, body=body)

    async def _fetch(
        self, request: Request, *, follow: bool
    ) -> tuple[int, dict[str, str], bytes, str | None]:
        method = request.method
        content = request.post_data_buffer
        headers = {
            name: value
            for name, value in (await request.all_headers()).items()
            if name.lower() not in DROPPED_REQUEST_HEADERS and not name.startswith(":")
        }
        current = request.url
        for _ in range(MAX_REDIRECTS + 1):
            addresses = await self._guard.check(current)
            with pinned(current, addresses):
                async with self._client.stream(
                    method, current, headers=headers, content=content, follow_redirects=False
                ) as response:
                    location = response.headers.get("location")
                    if response.status_code in REDIRECT_STATUSES and location:
                        target = urljoin(current, location)
                        if not follow:
                            return response.status_code, {}, b"", target
                        if response.status_code not in {307, 308}:
                            method, content = "GET", None
                        current = target
                        continue
                    body = await self._read(response)
            kept = {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in DROPPED_RESPONSE_HEADERS
            }
            return response.status_code, kept, body, None
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=None)

    async def _read(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise _ResponseTooLarge(f"response over {self._max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


async def _refuse_socket(socket: WebSocketRoute) -> None:
    # never connected to the server: a websocket would bypass the guarded request broker
    await socket.close(code=1008, reason="websockets are disabled")


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
        client: httpx.AsyncClient | None = None,
        max_pages: int = 2,
    ) -> None:
        self._agents = user_agents
        self._clock = clock
        self._guard = guard or SsrfGuard()
        self._classifier = classifier or BlockClassifier()
        self._owns_client = client is None
        self._client = client
        self._slots = asyncio.Semaphore(max_pages)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._start_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, proxy=NO_EGRESS_PROXY, args=list(LAUNCH_ARGS)
                )
            return self._browser

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                transport=PinnedTransport(), timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False
            )
        return self._client

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
                routes = _GuardedRoutes(self._http(), self._guard, ctx.max_bytes)
                await context.add_init_script(_NO_SOCKETS)
                await context.route_web_socket("**/*", _refuse_socket)
                await context.route("**/*", routes.handle)
                return await self._render_in(context, url, routes)
            finally:
                await context.close()

    async def _render_in(self, context: Any, url: str, routes: _GuardedRoutes) -> FetchResult:
        page = await context.new_page()
        target, chain = url, []
        for _ in range(MAX_REDIRECTS + 1):
            routes.redirect_to = None
            try:
                response = await page.goto(
                    target, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
                )
            except PlaywrightError as error:
                if routes.violation is not None:
                    raise routes.violation from None
                if routes.redirect_to is not None:
                    chain.append(target)
                    target = routes.redirect_to
                    # a hop to a non-http scheme would never reach the route handler
                    await self._guard.check(target)
                    continue
                outcome = FetchOutcome.timeout if "Timeout" in str(error) else FetchOutcome.error
                return self._result(url, target, outcome, None, None, {}, chain=chain)
            break
        else:
            return self._result(url, target, FetchOutcome.error, None, None, {}, chain=chain)
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_function(_TEXT_READY, timeout=TEXT_WAIT_MS)
        await page.evaluate(_STRIP_CONSENT, list(CONSENT_SELECTORS))
        html = await page.content()
        # any blocked hop or sub-request discards the page, and the url the page ended on is checked again
        if routes.violation is not None:
            raise routes.violation
        final_url = page.url
        await self._guard.check(final_url)
        status = response.status if response is not None else None
        headers = await response.all_headers() if response is not None else {}
        verdict = self._classifier.classify(
            status_code=status, headers=headers, body=html, content_type=headers.get("content-type")
        )
        return self._result(
            url, final_url, verdict.fetch_outcome, status, html, headers, verdict.block_reason, chain=chain
        )

    def _result(
        self,
        requested_url: str,
        final_url: str,
        outcome: FetchOutcome,
        status: int | None,
        html: str | None,
        headers: dict[str, str],
        block_reason: Any = None,
        *,
        chain: list[str],
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
            redirect_chain=chain,
            fetched_at=self._clock.now(),
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
