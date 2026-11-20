from collections.abc import Mapping

from app.pipeline.contracts import (
    BudgetExceeded,
    BudgetKind,
    RunBudget,
    RunCancelled,
    StageBudget,
    UsageLedger,
)

# budget kinds tracked in UsageLedger; llm_calls is enforced per stage only
_USAGE_FIELD: Mapping[BudgetKind, str] = {
    BudgetKind.youtube_units: "youtube_units",
    BudgetKind.search_calls: "search_calls",
    BudgetKind.llm_usd: "llm_usd",
    BudgetKind.tavily_credits: "tavily_credits",
    BudgetKind.pages: "http_pages",
}
_RUN_LIMIT_FIELD: Mapping[BudgetKind, str] = {
    BudgetKind.youtube_units: "youtube_units",
    BudgetKind.search_calls: "search_calls",
    BudgetKind.llm_usd: "llm_usd",
    BudgetKind.tavily_credits: "tavily_credits",
}


def add_usage(left: UsageLedger, right: UsageLedger) -> UsageLedger:
    totals = {name: getattr(left, name) + getattr(right, name) for name in UsageLedger.model_fields}
    return UsageLedger(**totals)


class RunBudgetLedger:
    """run-wide spend; stage ledgers draw on it so parallel stages share one budget."""

    def __init__(self, budget: RunBudget, spent: UsageLedger | None = None) -> None:
        self.budget = budget
        self._spent: dict[BudgetKind, float] = {kind: 0.0 for kind in BudgetKind}
        for kind, field in _USAGE_FIELD.items():
            self._spent[kind] = float(getattr(spent or UsageLedger(), field))
        self._carried = spent or UsageLedger()
        self._stages: list[StageBudgetLedger] = []

    def limit(self, kind: BudgetKind) -> float | None:
        field = _RUN_LIMIT_FIELD.get(kind)
        return None if field is None else float(getattr(self.budget, field))

    def spent(self, kind: BudgetKind) -> float:
        return self._spent[kind]

    def remaining(self, kind: BudgetKind) -> float | None:
        limit = self.limit(kind)
        return None if limit is None else max(limit - self._spent[kind], 0.0)

    def record(self, kind: BudgetKind, amount: float) -> None:
        self._spent[kind] += amount

    def for_stage(self, budget: StageBudget) -> "StageBudgetLedger":
        ledger = StageBudgetLedger(self, budget)
        self._stages.append(ledger)
        return ledger

    def usage(self) -> UsageLedger:
        total = self._carried
        for stage in self._stages:
            total = add_usage(total, stage.usage())
        return total


class StageBudgetLedger:
    def __init__(self, run: RunBudgetLedger, budget: StageBudget) -> None:
        self._run = run
        self._budget = budget
        self._spent: dict[BudgetKind, float] = {kind: 0.0 for kind in BudgetKind}

    def _stage_limit(self, kind: BudgetKind) -> float | None:
        value = getattr(self._budget, kind.value)
        return None if value is None else float(value)

    def remaining(self, kind: BudgetKind) -> float | None:
        candidates = []
        stage_limit = self._stage_limit(kind)
        if stage_limit is not None:
            candidates.append(max(stage_limit - self._spent[kind], 0.0))
        run_remaining = self._run.remaining(kind)
        if run_remaining is not None:
            candidates.append(run_remaining)
        return min(candidates) if candidates else None

    def ensure(self, kind: BudgetKind, amount: float) -> None:
        if amount < 0:
            raise ValueError("budget amounts must not be negative")
        remaining = self.remaining(kind)
        if remaining is not None and amount > remaining + 1e-9:
            raise BudgetExceeded(kind, requested=amount, remaining=remaining)

    def charge(self, kind: BudgetKind, amount: float) -> None:
        self.ensure(kind, amount)
        self._spent[kind] += amount
        self._run.record(kind, amount)

    def usage(self) -> UsageLedger:
        return UsageLedger(
            **{
                field: (
                    int(self._spent[kind])
                    if field not in {"llm_usd", "tavily_credits"}
                    else self._spent[kind]
                )
                for kind, field in _USAGE_FIELD.items()
            }
        )


class RunCancellation:
    """cooperative cancel flag; a watcher sets it from the redis key or the db column."""

    def __init__(self) -> None:
        self._reason: str | None = None

    def request(self, reason: str = "user") -> None:
        if self._reason is None:
            self._reason = reason

    @property
    def reason(self) -> str | None:
        return self._reason

    def is_cancelled(self) -> bool:
        return self._reason is not None

    async def checkpoint(self) -> None:
        if self._reason is not None:
            raise RunCancelled(self._reason)
