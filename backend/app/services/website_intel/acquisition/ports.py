from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.services.website_intel.models import PageVerdict


@dataclass(frozen=True)
class ReaderDocument:
    requested_url: str
    final_url: str
    title: str | None
    markdown: str
    verdict: PageVerdict
    status_code: int | None


class MarkdownReader(Protocol):
    # a third-party reader that fetches the page from its own egress and returns markdown
    name: str

    async def read(self, url: str) -> ReaderDocument: ...


@dataclass(frozen=True)
class ArchivedPage:
    original_url: str
    archive_url: str
    snapshot_at: datetime
    status_code: int
    content_type: str | None
    html: str


class WebArchive(Protocol):
    name: str

    async def latest(self, url: str) -> ArchivedPage | None: ...
