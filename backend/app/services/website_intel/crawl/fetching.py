import re
from dataclasses import dataclass, field
from typing import Protocol

from charset_normalizer import from_bytes

from app.domain.web import FetchResult, UserAgentMode
from app.ports.crawler import FetchContext
from app.services.website_intel.models import PageVerdict

_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)
_HEADER_CHARSET = re.compile(r"charset=([A-Za-z0-9_\-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class UserAgents:
    honest: str
    compat: str

    def for_mode(self, mode: UserAgentMode) -> str:
        return self.compat if mode is UserAgentMode.compat else self.honest


@dataclass
class RawResponse:
    requested_url: str
    final_url: str
    status_code: int | None
    headers: dict[str, str]
    body: bytes
    verdict: PageVerdict
    redirect_chain: list[str] = field(default_factory=list)
    error: str | None = None
    too_large: bool = False
    # extracted off the event loop by the fetcher; "" when the pdf had no readable text
    pdf_text: str | None = None

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")

    @property
    def is_pdf(self) -> bool:
        return "application/pdf" in (self.content_type or "") or self.body[:5] == b"%PDF-"

    @property
    def retry_after_s(self) -> float | None:
        value = self.headers.get("retry-after")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def decoded(self) -> str:
        return decode_body(self.body, self.content_type)


def decode_body(body: bytes, content_type: str | None) -> str:
    candidates: list[str] = []
    if content_type and (match := _HEADER_CHARSET.search(content_type)):
        candidates.append(match.group(1))
    if match := _META_CHARSET.search(body[:4096]):
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    for encoding in candidates:
        try:
            text = body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        # mis-declared charsets show up as replacement characters
        if text.count("�") <= len(text) / 100:
            return text
    best = from_bytes(body).best()
    return str(best) if best is not None else body.decode("utf-8", errors="replace")


class RawFetcher(Protocol):
    # a PageFetcher that also exposes status, headers and the verdict the crawler needs
    async def fetch_raw(
        self, url: str, ctx: FetchContext, *, etag: str | None = None, last_modified: str | None = None
    ) -> RawResponse: ...

    def to_result(self, raw: RawResponse) -> FetchResult: ...
