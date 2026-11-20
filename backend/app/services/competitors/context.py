import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.core.config import CompetitorBudgetSettings, CompetitorSettings
from app.domain.profile import BusinessProfile
from app.domain.topics import TopicRef
from app.domain.youtube import QuotaPriority
from app.pipeline.contracts import BudgetExceeded, BudgetKind, BudgetLedger
from app.ports.crawler import FetchContext
from app.ports.llm import CallContext, LLMResult, PayloadClass
from app.ports.youtube import RequestContext
from app.services.competitors.model import (
    DiscoveryUsage,
    DiscoveryWarning,
    KnownCandidate,
    UserCompetitorInput,
)

DiscoveryTrigger = Literal["onboarding", "scheduled", "profile_change", "manual"]
SearchPurpose = Literal["niche", "resolution"]


class DiscoveryMeter:
    """per-run spend guard for youtube units, search calls, tavily credits, llm dollars and wall time."""

    def __init__(
        self,
        budget: CompetitorBudgetSettings,
        max_resolution_searches: int,
        ledger: BudgetLedger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self.max_resolution_searches = max_resolution_searches
        self.ledger = ledger
        self._monotonic = monotonic
        self._started = monotonic()
        self.yt_units = 0
        self.search_calls = 0
        self.resolution_searches = 0
        self.tavily_credits = 0.0
        self.llm_tokens_in = 0
        self.llm_tokens_out = 0
        self.llm_usd = 0.0

    def _check(self, kind: BudgetKind, used: float, cap: float, amount: float) -> None:
        if used + amount > cap:
            raise BudgetExceeded(kind, amount, max(cap - used, 0))
        if self.ledger is not None:
            self.ledger.ensure(kind, amount)

    # calls run concurrently, so spend is reserved before the call and settled after it
    def check_youtube(self, units: int) -> None:
        self._check(BudgetKind.youtube_units, self.yt_units, self.budget.youtube_units, units)

    def reserve_youtube(self, units: int) -> None:
        self.check_youtube(units)
        self.yt_units += units
        if self.ledger is not None:
            self.ledger.charge(BudgetKind.youtube_units, units)

    def reserve_search(self, purpose: SearchPurpose) -> None:
        if purpose == "resolution" and self.resolution_searches >= self.max_resolution_searches:
            raise BudgetExceeded(BudgetKind.search_calls, 1, 0)
        self._check(BudgetKind.search_calls, self.search_calls, self.budget.search_calls, 1)
        self.search_calls += 1
        self.resolution_searches += purpose == "resolution"

    def settle_search(self, purpose: SearchPurpose, spent: bool) -> None:
        if spent:
            if self.ledger is not None:
                self.ledger.charge(BudgetKind.search_calls, 1)
            return
        self.search_calls -= 1
        self.resolution_searches -= purpose == "resolution"

    def reserve_tavily(self, credits: float) -> None:
        self._check(BudgetKind.tavily_credits, self.tavily_credits, self.budget.tavily_credits, credits)
        self.tavily_credits += credits

    def settle_tavily(self, reserved: float, spent: float) -> None:
        self.tavily_credits += spent - reserved
        if spent and self.ledger is not None:
            self.ledger.charge(BudgetKind.tavily_credits, spent)

    def ensure_llm(self) -> None:
        if self.llm_usd >= self.budget.llm_usd:
            raise BudgetExceeded(BudgetKind.llm_usd, 0.0, 0.0)

    def charge_llm(self, result: LLMResult) -> None:
        self.llm_tokens_in += result.usage.input_tokens + result.usage.cache_read_tokens
        self.llm_tokens_out += result.usage.output_tokens
        self.llm_usd += result.cost_usd
        if self.ledger is not None:
            self.ledger.charge(BudgetKind.llm_usd, result.cost_usd)

    def elapsed(self) -> float:
        return self._monotonic() - self._started

    def out_of_time(self) -> bool:
        return self.elapsed() >= self.budget.wall_seconds

    def usage(self) -> DiscoveryUsage:
        return DiscoveryUsage(
            yt_units=self.yt_units,
            search_calls=self.search_calls,
            tavily_credits=self.tavily_credits,
            llm_tokens_in=self.llm_tokens_in,
            llm_tokens_out=self.llm_tokens_out,
            llm_usd=round(self.llm_usd, 4),
            seconds=round(self.elapsed(), 3),
        )


@dataclass
class DiscoveryContext:
    workspace_id: UUID
    client_id: UUID
    discovery_run_id: UUID
    pipeline_run_id: UUID | None
    trigger: DiscoveryTrigger
    profile: BusinessProfile
    settings: CompetitorSettings
    meter: DiscoveryMeter
    now: datetime
    own_channel_ids: list[str] = field(default_factory=list)
    topic_refs: list[TopicRef] = field(default_factory=list)
    buyer_questions: list[str] = field(default_factory=list)
    user_inputs: list[UserCompetitorInput] = field(default_factory=list)
    known: list[KnownCandidate] = field(default_factory=list)
    enabled_sources: frozenset[str] | None = None
    warnings: list[DiscoveryWarning] = field(default_factory=list)
    degraded: set[str] = field(default_factory=set)

    def warn(self, code: str, message: str) -> None:
        if all(warning.code != code for warning in self.warnings):
            self.warnings.append(DiscoveryWarning(code=code, message=message[:200]))

    @property
    def priority(self) -> QuotaPriority:
        return (
            QuotaPriority.interactive
            if self.trigger in {"onboarding", "manual"}
            else QuotaPriority.background
        )

    @property
    def request_context(self) -> RequestContext:
        return RequestContext(client_id=self.client_id, run_id=self.pipeline_run_id, priority=self.priority)

    @property
    def fetch_context(self) -> FetchContext:
        return FetchContext(client_id=self.client_id, run_id=self.pipeline_run_id, timeout_s=15.0)

    def call_context(self, stage: str, *payload: PayloadClass) -> CallContext:
        return CallContext(
            run_id=self.pipeline_run_id,
            client_id=self.client_id,
            stage=stage,
            payload_classes=frozenset(payload or {PayloadClass.public_web}),
            language=self.profile.identity.primary_language or "en",
        )

    @property
    def market(self) -> str | None:
        identity = self.profile.identity
        if identity.primary_markets:
            return identity.primary_markets[0]
        return identity.country or (
            self.profile.locations.countries[0] if self.profile.locations.countries else None
        )

    @property
    def client_domains(self) -> set[str]:
        identity = self.profile.identity
        domains = set(identity.website_domains)
        if identity.website is not None:
            domains.add(identity.website.registrable_domain)
        return {domain.lower() for domain in domains if domain}

    def source_enabled(self, kind: str) -> bool:
        return self.enabled_sources is None or kind in self.enabled_sources
