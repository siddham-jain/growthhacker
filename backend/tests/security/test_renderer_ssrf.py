import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.adapters.crawler.playwright_renderer import PlaywrightRenderer
from app.core.clock import SystemClock
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.inputs.ssrf import SsrfGuard, UnsafeTargetError

pytest.importorskip("playwright")

PRIVATE_PATHS = ("/secret", "/wsprobe")
# enough body text that the renderer does not wait for a javascript shell to fill in
FILLER = b"<p>" + b"public words " * 40 + b"</p>"
PAGES = {
    "/wspage": FILLER + b"<script>try { new WebSocket('ws://' + location.host + '/wsprobe'); }"
    b" catch (e) {}</script>",
    "/xhrpage": FILLER + b"<script>fetch('/hop-to-secret').catch(() => {});</script>",
    "/plain": FILLER + b"<p>PUBLIC-CONTENT</p>",
}


class Recorder(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.hits.append(self.path)
        if self.path in {"/start", "/hop-to-secret"}:
            self.send_response(302)
            self.send_header("Location", "/secret")
            self.end_headers()
            return
        body = PAGES.get(self.path, b"INTERNAL-SECRET-METADATA")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)


class PathGuard(SsrfGuard):
    """treats the private paths as internal targets and pins everything else to the local server."""

    async def check(self, url: str) -> list[str]:
        if any(path in url for path in PRIVATE_PATHS) or not url.startswith("http"):
            raise UnsafeTargetError(url)
        return ["127.0.0.1"]


def _chromium_installed() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).exists()


pytestmark = pytest.mark.skipif(not _chromium_installed(), reason="chromium is not installed")


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    Recorder.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", Recorder.hits
    httpd.shutdown()


@pytest.fixture
async def renderer() -> PlaywrightRenderer:
    renderer = PlaywrightRenderer(user_agents=UserAgents("ua", "ua"), clock=SystemClock(), guard=PathGuard())
    yield renderer
    await renderer.aclose()


CTX = FetchContext(client_id=None, run_id=None)


async def test_redirect_to_an_internal_target_is_refused_before_it_is_fetched(server, renderer) -> None:
    base, hits = server
    with pytest.raises(UnsafeTargetError):
        await renderer.render(f"{base}/start", CTX)
    assert "/secret" not in hits


async def test_subresource_redirect_to_an_internal_target_discards_the_page(server, renderer) -> None:
    base, hits = server
    with pytest.raises(UnsafeTargetError):
        await renderer.render(f"{base}/xhrpage", CTX)
    assert "/hop-to-secret" in hits and "/secret" not in hits


async def test_websockets_never_reach_the_network(server, renderer) -> None:
    base, hits = server
    await renderer.render(f"{base}/wspage", CTX)
    await asyncio.sleep(0.5)
    assert not any(hit.startswith("/wsprobe") for hit in hits)


async def test_public_pages_still_render_through_the_guarded_broker(server, renderer) -> None:
    base, _hits = server
    result = await renderer.render(f"{base}/plain", CTX)
    assert result.html is not None and "PUBLIC-CONTENT" in result.html
    assert result.final_url == f"{base}/plain"
