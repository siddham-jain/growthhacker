from app.evaluation.ops.models import PRIMARY_BUCKETS, OpsReport, StageTiming
from app.evaluation.reporting.markdown import NA, num, pct, table

# master plan §8 figures; shown so an empty period still states what we expect, never as measurements
PLANNING_FIGURES = (
    ("YouTube main bucket per daily refresh", "~25-30 units (interim planning figure ≈50, 99 C56)"),
    ("YouTube search bucket per daily refresh", "~3-5 calls (0 when the niche cache is hit)"),
    ("batchGetStats per daily refresh", "~10-15 calls"),
    ("LLM per daily refresh", "~$0.20-0.30 (Haiku 4.5 + Sonnet 5.5)"),
    ("Tavily news credits per day", "niche-dependent (AI ≈ daily polling, dental ≈ weekly)"),
    ("Daily refresh wall time", "≈35 s typical, ≤110 s worst (target < 120 s)"),
    ("Onboarding to first review", "≤ 90 s: key pages only; the full crawl runs after the first analysis"),
    ("Onboarding wall time", "≤ 4 min, mostly hidden behind the review (tracked separately)"),
)
TARGET_LABELS = {
    "daily_refresh_p95_under_120s": "Daily refresh p95 wall time < 120 s (warm cache)",
    "onboarding_first_review_p95_under_90s": "Onboarding p95 time to first review ≤ 90 s",
    "onboarding_p95_under_4min": "Onboarding p95 wall time ≤ 4 min (tracked separately)",
    "units_per_client_within_budget": "Median main-bucket units per client-day within budget",
    "llm_prices_fresh": "LLM price table verified recently",
    "retention_compliant": "No API data past the retention limit",
}


def _seconds(ms: int | None) -> str:
    return NA if ms is None else f"{ms / 1000:.1f}"


def _status(value: bool | None) -> str:
    return "no data" if value is None else ("met" if value else "missed")


def _timing_rows(timings: list[StageTiming]) -> list[list[object]]:
    return [
        [t.stage, t.n, _seconds(t.p50_ms), _seconds(t.p95_ms), _seconds(t.max_ms), pct(t.failure_rate)]
        for t in timings
    ]


TIMING_HEADERS = ("stage", "n", "p50 s", "p95 s", "max s", "failure rate")


def render_ops_report(report: OpsReport) -> str:
    sections = [
        _header(report),
        _targets(report),
        _latency(report),
        _quota(report),
        _capacity(report),
        _llm(report),
        _tavily(report),
        _crawl(report),
        _failures(report),
        _retention(report),
        _planning(),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _header(report: OpsReport) -> str:
    first, last = report.quota_days_pt
    lines = [
        "# Operations report",
        "",
        f"- Period (UTC): {report.period_from.isoformat()} to {report.period_to.isoformat()}",
        f"- Quota days ({report.quota_timezone}, Google's reset): {first.isoformat()} to {last.isoformat()}",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Clients with activity: {report.clients}",
        f"- Measured runs present: {'yes' if report.measured else 'no'}",
        "- Every figure is calculated by GrowthHacker from its own ledgers; none is a YouTube metric.",
    ]
    lines += [f"- Note: {note}" for note in report.notes]
    return "\n".join(lines)


def _targets(report: OpsReport) -> str:
    rows = [[TARGET_LABELS.get(key, key), _status(value)] for key, value in report.targets.items()]
    return "## Targets\n\n" + table(("target", "status"), rows)


def _latency(report: OpsReport) -> str:
    parts = [
        "## Time to generate recommendations",
        "",
        "Definition (99 C69): wall time of a `daily_refresh` run from its first stage start to finalize, "
        "excluding queue wait, p95 < 120 s with a warm cache. Onboarding is reported separately.",
    ]
    refresh = report.run_time.get("daily_refresh")
    parts += ["", "### daily_refresh", ""]
    parts.append(
        table(TIMING_HEADERS, _timing_rows(refresh)) if refresh else "No daily_refresh runs measured."
    )
    for kind, timings in sorted(report.run_time.items()):
        if kind != "daily_refresh":
            parts += ["", f"### {kind}", "", table(TIMING_HEADERS, _timing_rows(timings))]
    if report.queue_wait:
        rows = [[kind, *_timing_rows([t])[0][1:5]] for kind, t in report.queue_wait.items()]
        parts += [
            "",
            "### Queue wait (not counted in wall time)",
            "",
            table(("run kind", *TIMING_HEADERS[1:5]), rows),
        ]
    return "\n".join(parts)


def _quota(report: OpsReport) -> str:
    parts = ["## Quota units per client per daily refresh", ""]
    if report.quota:
        headers = (
            "day (PT)",
            "client",
            "refresh runs",
            *(f"{b} / refresh" for b in PRIMARY_BUCKETS),
            *(f"{b} amortised / refresh" for b in PRIMARY_BUCKETS),
            "cache hit rate",
        )
        rows = [
            [
                item.day_pt.isoformat(),
                str(item.client_id),
                item.daily_refresh_runs,
                *(num(item.units_per_daily_refresh.get(b), 1) for b in PRIMARY_BUCKETS),
                *(num(item.amortised_per_daily_refresh.get(b), 1) for b in PRIMARY_BUCKETS),
                pct(item.cache_hit_rate),
            ]
            for item in report.quota
        ]
        parts.append(table(headers, rows))
        parts += [
            "",
            "Marginal units are spent inside the client's daily_refresh runs; amortised adds the client's "
            "share of shared-cache fills, split by that day's cache hits.",
        ]
    else:
        parts.append("No client quota usage recorded.")
    if report.project_quota:
        headers = ("day (PT)", "main", "search calls", "batch_stats", "analytics", "unattributed shared main")
        rows = [
            [
                day.day_pt.isoformat(),
                day.units.get("main", 0),
                day.units.get("search", 0),
                day.units.get("batch_stats", 0),
                day.units.get("analytics", 0),
                num(day.unattributed_shared.get("main"), 0),
            ]
            for day in report.project_quota
        ]
        parts += ["", "### Project totals (whole Google Cloud project)", "", table(headers, rows)]
    return "\n".join(parts)


def _capacity(report: OpsReport) -> str:
    rows = [[bucket, num(value, 1)] for bucket, value in report.capacity_estimate.items()]
    note = "Clients one project can refresh per day = bucket daily limit ÷ median amortised units per client-day."
    return "## Capacity estimate\n\n" + note + "\n\n" + table(("bucket", "clients per day"), rows)


def _client(client_id: object) -> str:
    return "project total" if client_id is None else str(client_id)


def _llm(report: OpsReport) -> str:
    if not report.llm:
        return "## LLM cost\n\nNo LLM calls recorded."
    headers = ("client", "calls", "usd", "usd / daily refresh", "retries", "errors", "price missing")
    rows = [
        [
            _client(s.client_id),
            s.calls,
            num(s.usd, 4),
            num(s.usd_per_daily_refresh, 4),
            s.retries,
            s.errors,
            s.price_missing,
        ]
        for s in report.llm
    ]
    total = report.llm[-1]
    models = [
        [
            key,
            t.calls,
            t.input_tokens,
            t.output_tokens,
            t.cache_read_tokens,
            t.cache_write_tokens,
            num(t.usd, 4),
        ]
        for key, t in total.by_model_operation.items()
    ]
    model_headers = ("model | operation", "calls", "input", "output", "cache read", "cache write", "usd")
    return "\n\n".join(
        [
            "## LLM cost",
            f"Prices from the versioned price table, last verified {report.prices_verified_at.isoformat()}.",
            table(headers, rows),
            table(model_headers, models),
        ]
    )


def _tavily(report: OpsReport) -> str:
    if not report.tavily:
        return "## Tavily credits\n\nNo Tavily calls recorded."
    rows = [[_client(u.client_id), u.calls, num(u.credits, 2), num(u.usd, 4)] for u in report.tavily]
    return "## Tavily credits\n\n" + table(("client", "calls", "credits", "usd"), rows)


def _crawl(report: OpsReport) -> str:
    crawl = report.crawl
    if crawl is None:
        return "## Crawl\n\nNo crawl ledger rows recorded."
    rows = [[crawl.calls, crawl.pages, pct(crawl.error_rate), _seconds(crawl.p95_latency_ms)]]
    outcomes = ", ".join(f"{k}: {v}" for k, v in crawl.outcomes.items())
    return (
        "## Crawl\n\n"
        + table(("calls", "pages", "error rate", "p95 latency s"), rows)
        + f"\n\nOutcomes: {outcomes}"
    )


def _failures(report: OpsReport) -> str:
    parts = ["## Failures"]
    rows = [
        [kind, t.failures, pct(t.failure_rate)]
        for kind, timings in sorted(report.run_time.items())
        for t in timings
        if t.stage == "total"
    ]
    parts.append(table(("run kind", "failed runs", "failure rate"), rows) if rows else "No runs recorded.")
    errors = [
        [provider, kind, count]
        for provider, kinds in report.provider_errors.items()
        for kind, count in kinds.items()
    ]
    if errors:
        parts.append(table(("provider", "error", "count"), errors))
    return "\n\n".join(parts)


def _retention(report: OpsReport) -> str:
    retention = report.retention
    status = "compliant" if retention.compliant else f"{len(retention.violations)} violation(s)"
    parts = [
        "## Retention compliance",
        f"{retention.rules_checked} API-data rules audited: {status}.",
    ]
    if retention.violations:
        parts.append("\n".join(f"- {violation}" for violation in retention.violations))
    return "\n\n".join(parts)


def _planning() -> str:
    return (
        "## Planning figures (estimates, not measurements)\n\nFrom MASTER_PLAN §8, per client.\n\n"
        + table(("item", "estimate"), PLANNING_FIGURES)
    )
