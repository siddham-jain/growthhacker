import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.domain.runs import EventPayload, RunNotice
from app.domain.web import CrawledPage, FetchResult, SiteRef, SourceKind, UserAgentMode
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.crawl.fetching import RawResponse
from app.services.website_intel.extract.fingerprint import content_hash
from app.services.website_intel.models import (
    CtaCandidate,
    ExtractedPage,
    NavItem,
    PageDetails,
    PageLink,
    PriceCandidate,
)

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
CLIENT_ID = uuid.UUID("0196b6f0-0000-7000-8000-000000000003")
APOLLO = SiteRef(
    input_url="apollohospitals.com",
    canonical_origin="https://www.apollohospitals.com",
    registrable_domain="apollohospitals.com",
    scope_hosts=["apollohospitals.com", "www.apollohospitals.com"],
)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@dataclass
class RecordingReporter:
    steps: list[tuple[str, str]] = field(default_factory=list)
    insights: list[EventPayload] = field(default_factory=list)
    notices: list[RunNotice] = field(default_factory=list)
    payloads: list[EventPayload] = field(default_factory=list)

    async def step(self, step: str, message: str, payload: EventPayload | None = None) -> None:
        self.steps.append((step, message))
        if payload is not None:
            self.payloads.append(payload)

    async def progress(self, done: int, total: int | None, unit: str) -> None: ...

    async def insight(self, message: str, payload: EventPayload) -> None:
        self.insights.append(payload)

    async def notice(self, notice: RunNotice) -> None:
        self.notices.append(notice)

    def names(self) -> list[str]:
        return [name for name, _ in self.steps]


@pytest.fixture
def reporter() -> RecordingReporter:
    return RecordingReporter()


def make_page(
    url: str,
    text: str = "",
    *,
    source: SourceKind = SourceKind.site_direct,
    page_type: str = "other",
    title: str | None = None,
    json_ld: Sequence[dict[str, Any]] = (),
    ctas: Sequence[CtaCandidate] = (),
    prices: Sequence[PriceCandidate] = (),
    nav: Sequence[NavItem] = (),
    links: Sequence[PageLink] = (),
    meta: Mapping[str, str] | None = None,
    lang: str | None = "en",
    youtube_embeds: Sequence[str] = (),
    snapshot_at: datetime | None = None,
    injection_flags: int = 0,
) -> ExtractedPage:
    page_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{url}:{text}")
    return ExtractedPage(
        page=CrawledPage(
            page_id=page_id,
            url=url,
            canonical_url=url,
            final_url=url,
            source_kind=source,
            status_code=200,
            content_type="text/html",
            lang=lang,
            title=title,
            text=text,
            content_hash=content_hash(text),
            json_ld=list(json_ld),
            page_type=page_type,
            injection_flags=injection_flags,
            fetched_at=NOW,
            snapshot_at=snapshot_at,
        ),
        details=PageDetails(
            page_id=page_id,
            links=list(links),
            nav=list(nav),
            ctas=list(ctas),
            prices=list(prices),
            meta=dict(meta or {}),
            youtube_embeds=list(youtube_embeds),
        ),
    )


def cta(type_: str, target: str, page_url: str, label: str = "", *, header: bool = False) -> CtaCandidate:
    medium = {"call": "phone", "whatsapp": "whatsapp", "email": "email"}.get(type_, "web")
    return CtaCandidate(
        type=type_, label=label or target, target=target, medium=medium, page_url=page_url, in_header=header
    )  # type: ignore[arg-type]


@dataclass
class Served:
    status: int | None
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    final_url: str | None = None
    compat_only: bool = False


class FakeRawFetcher:
    """serves canned responses per url; compat_only pages answer 403 to the honest agent like a waf."""

    def __init__(self, pages: Mapping[str, Served], *, clock: FrozenClock | None = None) -> None:
        self.pages = dict(pages)
        self.requests: list[tuple[str, UserAgentMode]] = []
        self._classifier = BlockClassifier()
        self._clock = clock or FrozenClock(NOW)

    async def fetch_raw(
        self, url: str, ctx: FetchContext, *, etag: str | None = None, last_modified: str | None = None
    ) -> RawResponse:
        self.requests.append((url, ctx.ua_mode))
        served = self.pages.get(url) or Served(404, b"<html><title>Not found</title></html>")
        if served.compat_only and ctx.ua_mode is UserAgentMode.honest:
            served = Served(
                403,
                b"<html><head><title>403 Forbidden</title></head><body><center>Microsoft-Azure-Application-Gateway/v2"
                b"</center></body></html>",
                {"content-type": "text/html"},
            )
        headers = {"content-type": "text/html; charset=utf-8", **served.headers}
        body_text = served.body.decode("utf-8", errors="replace")
        verdict = self._classifier.classify(
            status_code=served.status, headers=headers, body=body_text, content_type=headers["content-type"]
        )
        return RawResponse(
            requested_url=url,
            final_url=served.final_url or url,
            status_code=served.status,
            headers=headers,
            body=served.body,
            verdict=verdict,
        )

    def to_result(self, raw: RawResponse) -> FetchResult:
        html = raw.decoded() if raw.body and raw.verdict.value == "ok" else None
        return FetchResult(
            requested_url=raw.requested_url,
            final_url=raw.final_url,
            source_kind=SourceKind.site_direct,
            outcome=raw.verdict.fetch_outcome,
            status_code=raw.status_code,
            block_reason=raw.verdict.block_reason,
            content_type=raw.content_type,
            html=html,
            fetched_at=self._clock.now(),
        )


def html_page(title: str, body: str, *, links: Sequence[tuple[str, str]] = (), head: str = "") -> bytes:
    anchors = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in links)
    return (
        f"<html lang='en'><head><title>{title}</title>{head}</head><body>"
        f"<header><nav><ul>{anchors}</ul></nav></header><main><h1>{title}</h1>{body}</main></body></html>"
    ).encode()
