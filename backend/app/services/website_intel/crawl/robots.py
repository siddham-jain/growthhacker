from dataclasses import dataclass, field
from enum import StrEnum

from protego import Protego

MAX_ROBOTS_BYTES = 512 * 1024
MAX_CRAWL_DELAY_S = 30.0


class RobotsStatus(StrEnum):
    parsed = "parsed"
    allow_all = "allow_all"
    disallow_all = "disallow_all"


@dataclass(frozen=True)
class RobotsRules:
    status: RobotsStatus
    token: str
    body: str | None = None
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    status_code: int | None = None
    _parser: Protego | None = field(default=None, repr=False, compare=False)

    def can_fetch(self, url: str) -> bool:
        if self.status is RobotsStatus.allow_all:
            return True
        if self.status is RobotsStatus.disallow_all or self._parser is None:
            return False
        return bool(self._parser.can_fetch(url, self.token))

    def explicitly_disallows(self, url: str) -> bool:
        return self.status is RobotsStatus.parsed and not self.can_fetch(url)


def _decode(body: bytes | str) -> str:
    if isinstance(body, str):
        body = body.encode("utf-8", errors="replace")
    return body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace").lstrip("﻿")


def robots_from_response(status_code: int | None, body: bytes | str | None, *, token: str) -> RobotsRules:
    """rfc 9309 status handling, except that a waf 401/403 counts as disallow-all (plan 03 §5.3)."""
    if status_code is None or status_code in {401, 403, 429} or status_code >= 500:
        return RobotsRules(RobotsStatus.disallow_all, token, status_code=status_code)
    if not 200 <= status_code < 300 or body is None:
        return RobotsRules(RobotsStatus.allow_all, token, status_code=status_code)
    text = _decode(body)
    try:
        parser = Protego.parse(text)
        crawl_delay = parser.crawl_delay(token)
        sitemaps = list(parser.sitemaps)
    except Exception:
        # unparseable files are treated like an unavailable robots.txt, which allows all
        return RobotsRules(RobotsStatus.allow_all, token, body=text, status_code=status_code)
    return RobotsRules(
        RobotsStatus.parsed,
        token,
        body=text,
        crawl_delay=float(crawl_delay) if crawl_delay is not None else None,
        sitemaps=sitemaps,
        status_code=status_code,
        _parser=parser,
    )


def effective_delay(
    crawl_delay: float | None, min_delay_s: float, max_delay_s: float = MAX_CRAWL_DELAY_S
) -> float:
    if crawl_delay is None:
        return min_delay_s
    return min(max(crawl_delay, min_delay_s), max_delay_s)
