from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LinkStatusClass(StrEnum):
    ok = "ok"
    malformed = "malformed"
    dead = "dead"
    soft_404 = "soft_404"
    redirect = "redirect"
    unverifiable = "unverifiable"


BROKEN_CLASSES = frozenset({LinkStatusClass.malformed, LinkStatusClass.dead, LinkStatusClass.soft_404})


@dataclass(frozen=True, slots=True)
class LinkStatus:
    url_normalised: str
    status_class: LinkStatusClass
    checked_at: datetime
    http_status: int | None = None
    final_url: str | None = None
    error: str | None = None
    repaired_url: str | None = None

    @property
    def broken(self) -> bool:
        return self.status_class in BROKEN_CLASSES

    def describe(self, raw_url: str) -> str:
        if self.status_class is LinkStatusClass.malformed:
            return f"Malformed link ({self.error or 'invalid address'}): {raw_url}"
        if self.status_class is LinkStatusClass.soft_404:
            return f"Link lands on a 'not found' page: {raw_url}"
        detail = f"{self.http_status} response" if self.http_status else (self.error or "no response")
        return f"Dead link ({detail}): {raw_url}"
