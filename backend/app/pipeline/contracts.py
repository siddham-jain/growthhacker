from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.common import ChannelId, CountryCode, DomainModel, WindowDays
from app.domain.profile import FreshnessProfile
from app.domain.runs import RunKind, RunNotice, RunTrigger
from app.domain.topics import TopicRef
from app.ports.artifacts import ArtifactKey, ArtifactReader
from app.ports.clock import Clock
from app.ports.progress import ProgressReporter


class TransientError(Exception):
    def __init__(self, detail: str, retry_after_s: float | None = None) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


class RunCancelled(Exception):
    pass


class BudgetKind(StrEnum):
    youtube_units = "youtube_units"
    search_calls = "search_calls"
    llm_usd = "llm_usd"
    llm_calls = "llm_calls"
    tavily_credits = "tavily_credits"
    pages = "pages"


class BudgetExceeded(Exception):
    def __init__(self, kind: BudgetKind, requested: float, remaining: float) -> None:
        super().__init__(f"{kind.value} budget exceeded: requested {requested}, remaining {remaining}")
        self.kind = kind
        self.requested = requested
        self.remaining = remaining


class StageBudget(DomainModel):
    # none means the stage draws only on the run budget
    youtube_units: int | None = None
    search_calls: int | None = None
    llm_usd: float | None = None
    llm_calls: int | None = None
    tavily_credits: float | None = None
    pages: int | None = None


class RunBudget(DomainModel):
    wall_s: float = Field(gt=0)
    youtube_units: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    llm_usd: float = Field(ge=0)
    tavily_credits: float = Field(ge=0)


class UsageLedger(DomainModel):
    youtube_units: int = 0
    search_calls: int = 0
    batch_stats_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cache_read_tokens: int = 0
    llm_cache_write_tokens: int = 0
    llm_usd: float = 0.0
    tavily_credits: float = 0.0
    http_pages: int = 0


class BudgetLedger(Protocol):
    # charge and ensure raise BudgetExceeded when the stage or run budget would be exceeded
    def ensure(self, kind: BudgetKind, amount: float) -> None: ...

    def charge(self, kind: BudgetKind, amount: float) -> None: ...

    def remaining(self, kind: BudgetKind) -> float | None: ...

    # the run-wide remainder, ignoring the stage's own limit
    def run_remaining(self, kind: BudgetKind) -> float | None: ...

    def usage(self) -> UsageLedger: ...


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...

    # raises RunCancelled once cancellation was requested
    async def checkpoint(self) -> None: ...


class Market(DomainModel):
    # none until onboarding infers it from the site, unless the user picked it
    country: CountryCode | None = None
    language: str
    timezone: str


class RunConfigSnapshot(DomainModel):
    # frozen at run creation and immutable for the run
    workspace_id: UUID
    client_id: UUID
    config_version: int
    config_hash: str
    window_days: WindowDays
    windows_computed: list[WindowDays] = [7, 30, 90]
    own_channel_id: ChannelId | None = None
    website_url: str | None = None
    # what the user typed, so onboarding can locate a business from its name or an unverified channel
    business_name: str | None = None
    channel_ref: str | None = None
    competitor_channel_ids: list[ChannelId] = []
    topic_refs: list[TopicRef] = []
    topic_set_version: int
    freshness_profile: FreshnessProfile | None = None
    profile_version: int | None = None
    oauth_connected: bool = False
    market: Market
    # the user picked the market; otherwise onboarding replaces it with what the site's evidence says
    market_explicit: bool = False
    feature_flags: dict[str, bool] = {}


@dataclass(frozen=True, slots=True)
class StagePolicy:
    timeout_s: float
    soft_timeout_s: float | None = None
    max_attempts: int = 2
    backoff_s: tuple[float, ...] = (2.0, 8.0)
    retry_on: tuple[type[Exception], ...] = (TransientError,)
    critical: bool = True
    idempotent: bool = True
    reusable: bool = True
    budget: StageBudget = field(default_factory=StageBudget)

    def __post_init__(self) -> None:
        if not self.idempotent:
            raise ValueError("every stage must be idempotent")
        if self.soft_timeout_s is not None and self.soft_timeout_s >= self.timeout_s:
            raise ValueError("soft_timeout_s must be below timeout_s")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


StageOutcome = Literal["succeeded", "degraded", "skipped"]


@dataclass(frozen=True, slots=True)
class StageResult[TOut: BaseModel]:
    status: StageOutcome
    artifact: TOut | None
    counts: Mapping[str, int] = field(default_factory=dict)
    notices: tuple[RunNotice, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "succeeded" and self.artifact is None:
            raise ValueError("a succeeded stage must produce its artifact")


@dataclass(frozen=True, slots=True)
class RunContext[DepsT]:
    # one per run; stages read inputs here and never touch the wall clock
    run_id: UUID
    kind: RunKind
    trigger: RunTrigger
    attempt: int
    as_of: datetime
    snapshot: RunConfigSnapshot
    clock: Clock
    budget: BudgetLedger
    reporter: ProgressReporter
    artifacts: ArtifactReader
    cancel: CancellationToken
    deps: DepsT
    stage: str | None = None
    soft_deadline: float | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

    @property
    def workspace_id(self) -> UUID:
        return self.snapshot.workspace_id

    @property
    def client_id(self) -> UUID:
        return self.snapshot.client_id

    @property
    def window_days(self) -> int:
        return self.snapshot.window_days

    @property
    def windows(self) -> tuple[int, ...]:
        return tuple(self.snapshot.windows_computed)

    def for_stage(
        self, name: str, reporter: ProgressReporter, soft_deadline: float | None = None
    ) -> "RunContext[DepsT]":
        return replace(self, stage=name, reporter=reporter, soft_deadline=soft_deadline)

    async def checkpoint(self) -> None:
        await self.cancel.checkpoint()


class Stage[TOut: BaseModel, DepsT](Protocol):
    name: str
    version: int
    depends_on: tuple[str, ...]
    optional_deps: tuple[str, ...]
    produces: ArtifactKey[TOut]
    policy: StagePolicy
    weight: float

    def enabled(self, snapshot: RunConfigSnapshot) -> bool: ...

    # extra inputs beyond upstream artifact fingerprints, json-serialisable
    def fingerprint_inputs(self, ctx: RunContext[DepsT]) -> Mapping[str, Any]: ...

    async def run(self, ctx: RunContext[DepsT]) -> StageResult[TOut]: ...
