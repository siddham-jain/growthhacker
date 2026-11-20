import asyncio
from urllib.parse import urljoin

import httpx

from app.adapters.crawler.pdf_extractor import PdfTextExtractor, PdfUnreadableError
from app.domain.web import FetchOutcome, FetchResult, SourceKind
from app.ports.clock import Clock
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.crawl.fetching import RawResponse, UserAgents, decode_body
from app.services.website_intel.inputs.ssrf import DnsLookupError, SsrfGuard, UnsafeTargetError
from app.services.website_intel.models import PageVerdict

MAX_REDIRECTS = 5
BOMB_RATIO = 20
ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"


class DirectHttpFetcher:
    """httpx fetch with redirect-by-redirect ssrf checks, size caps and block classification."""

    name = "direct"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agents: UserAgents,
        clock: Clock,
        guard: SsrfGuard | None = None,
        classifier: BlockClassifier | None = None,
        pdf_extractor: PdfTextExtractor | None = None,
        accept_language: str = "en;q=0.9,*;q=0.5",
        max_pdf_bytes: int = 10_000_000,
    ) -> None:
        self._client = client
        self._agents = user_agents
        self._clock = clock
        self._guard = guard or SsrfGuard()
        self._classifier = classifier or BlockClassifier()
        self._pdf = pdf_extractor or PdfTextExtractor()
        self._accept_language = accept_language
        self._max_pdf_bytes = max_pdf_bytes

    async def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        return self.to_result(await self.fetch_raw(url, ctx))

    async def fetch_raw(
        self, url: str, ctx: FetchContext, *, etag: str | None = None, last_modified: str | None = None
    ) -> RawResponse:
        headers = {
            "User-Agent": self._agents.for_mode(ctx.ua_mode),
            "Accept": ACCEPT,
            "Accept-Language": self._accept_language,
            "Accept-Encoding": "gzip, deflate",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            async with asyncio.timeout(ctx.timeout_s):
                return await self._follow(url, headers, ctx)
        except (UnsafeTargetError, DnsLookupError):
            raise
        except (TimeoutError, httpx.HTTPError) as error:
            return RawResponse(url, url, None, {}, b"", PageVerdict.error, error=type(error).__name__)

    async def _follow(self, url: str, headers: dict[str, str], ctx: FetchContext) -> RawResponse:
        chain: list[str] = []
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await self._guard.check(current)
            async with self._client.stream(
                "GET", current, headers=headers, follow_redirects=False
            ) as response:
                location = response.headers.get("location")
                if response.is_redirect and location:
                    chain.append(current)
                    current = urljoin(current, location)
                    continue
                cap = (
                    self._max_pdf_bytes
                    if "pdf" in response.headers.get("content-type", "")
                    else ctx.max_bytes
                )
                body, too_large = await self._read_capped(response, cap)
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                verdict = self._classifier.classify(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=decode_body(body, response_headers.get("content-type")) if body else None,
                    content_type=response_headers.get("content-type"),
                )
                return RawResponse(
                    requested_url=url,
                    final_url=current,
                    status_code=response.status_code,
                    headers=response_headers,
                    body=body,
                    verdict=verdict,
                    redirect_chain=chain,
                    too_large=too_large,
                )
        return RawResponse(url, current, None, {}, b"", PageVerdict.error, chain, error="redirect_loop")

    @staticmethod
    async def _read_capped(response: httpx.Response, cap: int) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            compressed = max(response.num_bytes_downloaded, 1)
            if size > cap or (size > cap // 4 and size / compressed > BOMB_RATIO):
                return b"".join(chunks), True
            chunks.append(chunk)
        return b"".join(chunks), False

    def to_result(self, raw: RawResponse) -> FetchResult:
        outcome = raw.verdict.fetch_outcome
        if raw.too_large:
            outcome = FetchOutcome.too_large
        elif raw.error == "TimeoutError" or raw.error == "ReadTimeout":
            outcome = FetchOutcome.timeout
        html: str | None = None
        text: str | None = None
        if outcome is FetchOutcome.ok and raw.is_pdf:
            text = self._pdf_text(raw.body)
            outcome = FetchOutcome.ok if text else FetchOutcome.non_html
        elif outcome is FetchOutcome.ok and "html" in (raw.content_type or "html"):
            html = raw.decoded()
        elif outcome is FetchOutcome.ok:
            outcome = FetchOutcome.non_html
        return FetchResult(
            requested_url=raw.requested_url,
            final_url=raw.final_url,
            source_kind=SourceKind.site_direct,
            outcome=outcome,
            status_code=raw.status_code,
            block_reason=raw.verdict.block_reason,
            content_type=raw.content_type,
            html=html,
            text=text,
            redirect_chain=raw.redirect_chain,
            etag=raw.headers.get("etag"),
            last_modified=raw.headers.get("last-modified"),
            fetched_at=self._clock.now(),
        )

    def _pdf_text(self, body: bytes) -> str | None:
        try:
            return self._pdf.extract(body)
        except PdfUnreadableError:
            return None
