from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

# the three youtube data api buckets the ops report leads with; analytics (oauth) is reported beside them
PRIMARY_BUCKETS = ("main", "search", "batch_stats")


class _OpsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StageTiming(_OpsModel):
    stage: str
    n: int
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None
    failures: int
    failure_rate: float | None


class BucketUsage(_OpsModel):
    units_client: int = 0
    units_daily_refresh: int = 0
    units_shared_share: float = 0.0

    @computed_field
    @property
    def units_amortised(self) -> float:
        return self.units_client + self.units_shared_share


class QuotaUsageSummary(_OpsModel):
    """one pacific-time quota day for one client; amortised = own units + share of shared-cache fills."""

    day_pt: date
    client_id: UUID
    buckets: dict[str, BucketUsage]
    daily_refresh_runs: int
    units_per_daily_refresh: dict[str, float | None]
    amortised_per_daily_refresh: dict[str, float | None]
    by_endpoint: dict[str, int]
    cache_hits: int
    cache_hit_rate: float | None


class ProjectQuotaDay(_OpsModel):
    day_pt: date
    units: dict[str, int]
    unattributed_shared: dict[str, float]
    by_endpoint: dict[str, int]
    errors: dict[str, int]


class TokenCounts(_OpsModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0


class LlmUsageSummary(_OpsModel):
    client_id: UUID | None
    calls: int
    usd: float
    usd_per_daily_refresh: float | None
    daily_refresh_runs: int
    retries: int
    errors: int
    price_missing: int
    by_model_operation: dict[str, TokenCounts]


class TavilyUsage(_OpsModel):
    client_id: UUID | None
    calls: int
    credits: float
    usd: float
    by_operation: dict[str, float]


class CrawlUsage(_OpsModel):
    calls: int
    pages: int
    error_rate: float | None
    p95_latency_ms: int | None
    outcomes: dict[str, int]


class RetentionStatus(_OpsModel):
    compliant: bool
    violations: list[str]
    oldest_age_days: dict[str, float]
    rules_checked: int


class OpsReport(_OpsModel):
    period_from: datetime
    period_to: datetime
    quota_days_pt: tuple[date, date]
    quota_timezone: str
    generated_at: datetime
    clients: int
    quota: list[QuotaUsageSummary]
    project_quota: list[ProjectQuotaDay]
    run_time: dict[str, list[StageTiming]]
    queue_wait: dict[str, StageTiming]
    llm: list[LlmUsageSummary]
    tavily: list[TavilyUsage]
    crawl: CrawlUsage | None
    provider_errors: dict[str, dict[str, int]]
    retention: RetentionStatus
    capacity_estimate: dict[str, float | None]
    targets: dict[str, bool | None]
    prices_verified_at: date
    measured: bool
    notes: list[str] = []
