"""pure aggregation of ledger rows into the ops report objects; no database access."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from statistics import median
from uuid import UUID
from zoneinfo import ZoneInfo

import numpy as np

from app.evaluation.ops.models import (
    BucketUsage,
    CrawlUsage,
    LlmUsageSummary,
    ProjectQuotaDay,
    QuotaUsageSummary,
    StageTiming,
    TavilyUsage,
    TokenCounts,
)
from app.evaluation.ops.sources import (
    COMPLETED_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    TIMED_STAGE_STATUSES,
    CacheHitRow,
    ProviderRow,
    QuotaRow,
    RunRow,
    StageRow,
)
from app.models.ledgers import QUOTA_BUCKETS

DAILY_REFRESH = "daily_refresh"
TOTAL = "total"


def percentile_ms(values: Sequence[int], q: float) -> int | None:
    if not values:
        return None
    return int(round(float(np.quantile(np.asarray(values, dtype=float), q))))


def timing(stage: str, durations: Sequence[int], failures: int, attempts: int) -> StageTiming:
    return StageTiming(
        stage=stage,
        n=len(durations),
        p50_ms=percentile_ms(durations, 0.5),
        p95_ms=percentile_ms(durations, 0.95),
        max_ms=max(durations) if durations else None,
        failures=failures,
        failure_rate=failures / attempts if attempts else None,
    )


def refresh_runs_by_day(runs: Iterable[RunRow], tz: ZoneInfo) -> Counter[tuple[date, UUID]]:
    counts: Counter[tuple[date, UUID]] = Counter()
    for run in runs:
        if run.kind == DAILY_REFRESH and run.started_at is not None:
            counts[(run.started_at.astimezone(tz).date(), run.client_id)] += 1
    return counts


def quota_summaries(
    rows: Sequence[QuotaRow], hits: Sequence[CacheHitRow], refresh_runs: Counter[tuple[date, UUID]]
) -> tuple[list[QuotaUsageSummary], list[ProjectQuotaDay]]:
    """shared (client-less) units are split across clients in proportion to that day's cache hits."""
    client_units: dict[tuple[date, UUID], dict[str, list[int]]] = defaultdict(
        lambda: {bucket: [0, 0] for bucket in QUOTA_BUCKETS}
    )
    calls: Counter[tuple[date, UUID]] = Counter()
    endpoints: dict[tuple[date, UUID], Counter[str]] = defaultdict(Counter)
    shared: dict[date, Counter[str]] = defaultdict(Counter)
    project: dict[date, Counter[str]] = defaultdict(Counter)
    project_endpoints: dict[date, Counter[str]] = defaultdict(Counter)
    errors: dict[date, Counter[str]] = defaultdict(Counter)
    for row in rows:
        project[row.day_pt][row.bucket] += row.units
        project_endpoints[row.day_pt][row.endpoint] += row.units
        if row.errors:
            errors[row.day_pt][row.endpoint] += row.errors
        if row.client_id is None:
            shared[row.day_pt][row.bucket] += row.units
            continue
        key = (row.day_pt, row.client_id)
        client_units[key][row.bucket][0] += row.units
        if row.run_kind == DAILY_REFRESH:
            client_units[key][row.bucket][1] += row.units
        calls[key] += row.calls
        endpoints[key][row.endpoint] += row.units
    hits_by_key = {(h.day_pt, h.client_id): h.hits for h in hits if h.hits > 0}
    hits_by_day: Counter[date] = Counter()
    for (day, _), count in hits_by_key.items():
        hits_by_day[day] += count
    keys = sorted(set(client_units) | set(hits_by_key) | set(refresh_runs), key=lambda k: (k[0], str(k[1])))
    summaries = []
    for key in keys:
        day, client = key
        hit_share = hits_by_key.get(key, 0) / hits_by_day[day] if hits_by_day[day] else 0.0
        units = client_units.get(key) or {bucket: [0, 0] for bucket in QUOTA_BUCKETS}
        buckets = {
            bucket: BucketUsage(
                units_client=own,
                units_daily_refresh=refresh,
                units_shared_share=shared[day][bucket] * hit_share,
            )
            for bucket, (own, refresh) in units.items()
        }
        runs = refresh_runs.get(key, 0)
        hit_count = hits_by_key.get(key, 0)
        lookups = hit_count + calls.get(key, 0)
        summaries.append(
            QuotaUsageSummary(
                day_pt=day,
                client_id=client,
                buckets=buckets,
                daily_refresh_runs=runs,
                units_per_daily_refresh={
                    b: u.units_daily_refresh / runs if runs else None for b, u in buckets.items()
                },
                amortised_per_daily_refresh={
                    b: (u.units_daily_refresh + u.units_shared_share) / runs if runs else None
                    for b, u in buckets.items()
                },
                by_endpoint=dict(sorted(endpoints.get(key, Counter()).items())),
                cache_hits=hit_count,
                cache_hit_rate=hit_count / lookups if lookups else None,
            )
        )
    days = []
    for day in sorted(project):
        days.append(
            ProjectQuotaDay(
                day_pt=day,
                units={bucket: project[day][bucket] for bucket in QUOTA_BUCKETS},
                unattributed_shared={
                    bucket: 0.0 if hits_by_day[day] else float(shared[day][bucket])
                    for bucket in QUOTA_BUCKETS
                },
                by_endpoint=dict(sorted(project_endpoints[day].items())),
                errors=dict(sorted(errors[day].items())),
            )
        )
    return summaries, days


def run_timings(runs: Sequence[RunRow], stages: Sequence[StageRow]) -> dict[str, list[StageTiming]]:
    by_kind: dict[str, dict[str, list[StageRow]]] = defaultdict(lambda: defaultdict(list))
    for row in stages:
        by_kind[row.run_kind][row.stage].append(row)
    report: dict[str, list[StageTiming]] = {}
    for kind in sorted({run.kind for run in runs} | set(by_kind)):
        items = []
        for stage, rows in sorted(by_kind[kind].items()):
            durations = [
                r.duration_ms for r in rows if r.status in TIMED_STAGE_STATUSES and r.duration_ms is not None
            ]
            failures = sum(r.status == "failed" for r in rows)
            items.append(timing(stage, durations, failures, len(rows)))
        kind_runs = [run for run in runs if run.kind == kind]
        walls = [
            run.wall_ms
            for run in kind_runs
            if run.status in COMPLETED_RUN_STATUSES and run.wall_ms is not None
        ]
        terminal = [run for run in kind_runs if run.status in TERMINAL_RUN_STATUSES]
        items.append(timing(TOTAL, walls, sum(run.status == "failed" for run in terminal), len(terminal)))
        report[kind] = items
    return report


def queue_waits(runs: Sequence[RunRow]) -> dict[str, StageTiming]:
    waits: dict[str, list[int]] = defaultdict(list)
    for run in runs:
        if run.queued_ms is not None:
            waits[run.kind].append(run.queued_ms)
    return {kind: timing("queue_wait", values, 0, len(values)) for kind, values in sorted(waits.items())}


def refresh_run_counts(runs: Iterable[RunRow]) -> Counter[UUID]:
    return Counter(run.client_id for run in runs if run.kind == DAILY_REFRESH)


def llm_summaries(rows: Sequence[ProviderRow], refresh_runs: Counter[UUID]) -> list[LlmUsageSummary]:
    groups: dict[UUID | None, list[ProviderRow]] = defaultdict(list)
    for row in rows:
        groups[row.client_id].append(row)
    summaries = [
        _llm(client, items, refresh_runs.get(client, 0)) for client, items in groups.items() if client
    ]
    summaries.sort(key=lambda s: str(s.client_id))
    if rows:
        summaries.append(_llm(None, list(rows), sum(refresh_runs.values())))
    return summaries


def _llm(client: UUID | None, rows: Sequence[ProviderRow], runs: int) -> LlmUsageSummary:
    by_key: dict[str, list[ProviderRow]] = defaultdict(list)
    for row in rows:
        by_key[f"{row.model or 'unknown'}|{row.operation}"].append(row)
    refresh_usd = sum(row.usd for row in rows if row.run_kind == DAILY_REFRESH)
    return LlmUsageSummary(
        client_id=client,
        calls=sum(r.calls for r in rows),
        usd=round(sum(r.usd for r in rows), 6),
        usd_per_daily_refresh=round(refresh_usd / runs, 6) if runs else None,
        daily_refresh_runs=runs,
        retries=sum(r.retries for r in rows),
        errors=sum(r.calls for r in rows if r.outcome != "ok"),
        price_missing=sum(r.price_missing for r in rows),
        by_model_operation={key: _tokens(items) for key, items in sorted(by_key.items())},
    )


def _tokens(rows: Sequence[ProviderRow]) -> TokenCounts:
    return TokenCounts(
        calls=sum(r.calls for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        cache_read_tokens=sum(r.cache_read_tokens for r in rows),
        cache_write_tokens=sum(r.cache_write_tokens for r in rows),
        usd=round(sum(r.usd for r in rows), 6),
    )


def tavily_usage(rows: Sequence[ProviderRow], usd_per_credit: float) -> list[TavilyUsage]:
    groups: dict[UUID | None, list[ProviderRow]] = defaultdict(list)
    for row in rows:
        groups[row.client_id].append(row)
    usage = [_tavily(client, items, usd_per_credit) for client, items in groups.items() if client]
    usage.sort(key=lambda u: str(u.client_id))
    if rows:
        usage.append(_tavily(None, list(rows), usd_per_credit))
    return usage


def _tavily(client: UUID | None, rows: Sequence[ProviderRow], usd_per_credit: float) -> TavilyUsage:
    credits = sum(r.credits for r in rows)
    operations: Counter[str] = Counter()
    for row in rows:
        operations[row.operation] += row.credits
    return TavilyUsage(
        client_id=client,
        calls=sum(r.calls for r in rows),
        credits=round(credits, 2),
        usd=round(credits * usd_per_credit, 4),
        by_operation=dict(sorted(operations.items())),
    )


def crawl_usage(rows: Sequence[ProviderRow], p95_latency_ms: int | None) -> CrawlUsage | None:
    if not rows:
        return None
    outcomes: Counter[str] = Counter()
    for row in rows:
        outcomes[row.outcome] += row.calls
    calls = sum(outcomes.values())
    return CrawlUsage(
        calls=calls,
        pages=sum(r.pages for r in rows),
        error_rate=(calls - outcomes.get("ok", 0)) / calls if calls else None,
        p95_latency_ms=p95_latency_ms,
        outcomes=dict(sorted(outcomes.items())),
    )


def provider_errors(
    rows: Sequence[ProviderRow], quota_days: Sequence[ProjectQuotaDay]
) -> dict[str, dict[str, int]]:
    errors: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row.outcome != "ok":
            errors[row.provider][row.error_kind or row.outcome] += row.calls
    for day in quota_days:
        for endpoint, count in day.errors.items():
            errors["youtube"][endpoint] += count
    return {provider: dict(sorted(counts.items())) for provider, counts in sorted(errors.items())}


def median_or_none(values: Sequence[float]) -> float | None:
    return float(median(values)) if values else None
