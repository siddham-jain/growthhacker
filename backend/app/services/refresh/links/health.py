import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.core.config import RefreshSettings
from app.ports.clock import Clock
from app.services.refresh.links.checker import FIRST_5XX, LinkProbe
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.links.urls import malformation, normalise_url


@dataclass(frozen=True, slots=True)
class CachedLink:
    status: LinkStatus
    next_check_at: datetime


class LinkStatusCache(Protocol):
    async def get_many(self, keys: Sequence[str]) -> dict[str, CachedLink]: ...

    async def put_many(self, entries: Sequence[CachedLink]) -> None: ...


@dataclass(slots=True)
class LinkHealthReport:
    statuses: dict[str, LinkStatus] = field(default_factory=dict)
    probed: int = 0
    deferred: int = 0


class LinkHealthService:
    """syntax first, then the shared 7-day cache, then a time-boxed, budgeted probe of the highest-value links."""

    def __init__(
        self,
        settings: RefreshSettings,
        clock: Clock,
        probe: LinkProbe | None = None,
        cache: LinkStatusCache | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.probe = probe
        self.cache = cache

    async def check(self, urls: Sequence[tuple[str, float]]) -> LinkHealthReport:
        report = LinkHealthReport()
        best: dict[str, tuple[float, str]] = {}
        for raw, priority in urls:
            key = normalise_url(raw)
            problem = malformation(raw)
            if problem is not None:
                report.statuses[key] = LinkStatus(
                    url_normalised=key,
                    status_class=LinkStatusClass.malformed,
                    checked_at=self.clock.now(),
                    error=problem.reason,
                    repaired_url=problem.repaired,
                )
                if problem.repaired:
                    raw, key = problem.repaired, normalise_url(problem.repaired)
                else:
                    continue
            if key not in best or priority > best[key][0]:
                best[key] = (priority, raw)
        cached = await self.cache.get_many(list(best)) if self.cache else {}
        now = self.clock.now()
        pending = []
        for key, (priority, raw) in best.items():
            entry = cached.get(key)
            if entry is not None and entry.next_check_at > now:
                report.statuses[key] = entry.status
            else:
                pending.append((priority, raw, entry.status if entry else None))
        if self.probe is None or not pending:
            report.deferred = len(pending)
            return report
        pending.sort(key=lambda item: item[0], reverse=True)
        budgeted = pending[: self.settings.link_max_probes]
        report.deferred = len(pending) - len(budgeted)
        tasks = [asyncio.create_task(self.probe.probe(raw, previous)) for _, raw, previous in budgeted]
        done, unfinished = await asyncio.wait(tasks, timeout=self.settings.link_time_box_s)
        for task in unfinished:
            task.cancel()
        report.deferred += len(unfinished)
        fresh = []
        for task in done:
            if task.cancelled() or task.exception() is not None:
                report.deferred += 1
                continue
            status = task.result()
            report.statuses[status.url_normalised] = status
            fresh.append(CachedLink(status=status, next_check_at=self._next_check(status)))
        report.probed = len(fresh)
        if self.cache and fresh:
            await self.cache.put_many(fresh)
        return report

    def _next_check(self, status: LinkStatus) -> datetime:
        if status.error == FIRST_5XX:
            return status.checked_at + timedelta(hours=self.settings.link_recheck_5xx_hours)
        return status.checked_at + timedelta(days=self.settings.link_cache_days)
