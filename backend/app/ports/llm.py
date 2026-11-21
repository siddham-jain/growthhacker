from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.common import DomainModel

Tier = Literal["low", "medium", "high"]


class TaskId(StrEnum):
    website_page_selection = "website_page_selection"
    website_page_map = "website_page_map"
    website_profile_reduce = "website_profile_reduce"
    website_keyword_enrichment = "website_keyword_enrichment"
    website_freshness_profile = "website_freshness_profile"
    competitor_candidate_generation = "competitor_candidate_generation"
    competitor_relation_classification = "competitor_relation_classification"
    competitor_entity_extraction = "competitor_entity_extraction"
    competitor_ownership_adjudication = "competitor_ownership_adjudication"
    competitor_owner_kind = "competitor_owner_kind"
    competitor_evidence_summary = "competitor_evidence_summary"
    topic_adjudication = "topic_adjudication"
    topic_alias_proposal = "topic_alias_proposal"
    directness_tiebreak = "directness_tiebreak"
    news_judgement = "news_judgement"
    refresh_phrasing = "refresh_phrasing"
    card_write = "card_write"
    card_critique = "card_critique"
    research_query_planning = "research_query_planning"
    trend_keywords = "trend_keywords"
    trend_ideas = "trend_ideas"


class PayloadClass(StrEnum):
    # what a prompt contains; gates which providers may receive it
    public_youtube = "public_youtube"
    public_web = "public_web"
    news = "news"
    client_profile = "client_profile"
    comments_redacted = "comments_redacted"
    client_private_analytics = "client_private_analytics"


class PromptRef(DomainModel):
    prompt_id: str
    version: str = "active"


class ClientContextBlock(DomainModel):
    kind: Literal["client_context"] = "client_context"
    text: str


class TrustedBlock(DomainModel):
    kind: Literal["trusted"] = "trusted"
    label: str
    text: str


class UntrustedBlock(DomainModel):
    # wrapped as data with a per-request boundary; never treated as instructions
    kind: Literal["untrusted"] = "untrusted"
    label: str
    source: str
    text: str


class PromptInput(DomainModel):
    client_context: ClientContextBlock | None = None
    trusted: list[TrustedBlock] = []
    untrusted: list[UntrustedBlock] = []


class CallContext(DomainModel):
    run_id: UUID | None
    client_id: UUID | None
    stage: str
    deadline: float | None = None
    payload_classes: frozenset[PayloadClass]
    idempotency_key: str | None = None
    language: str = "en"


class CallBudget(DomainModel):
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    max_attempts: int = Field(default=4, ge=1)


class TokenUsage(DomainModel):
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int | None = None


class LLMResult[T](BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: T
    raw_text: str
    task: TaskId
    prompt_id: str
    prompt_version: str
    provider: str
    model: str
    route_index: int
    attempts: int
    repaired: bool
    usage: TokenUsage
    cost_usd: float
    latency_ms: int
    stop_reason: str
    request_ids: list[str] = []
    cache_hit: bool = False


class LLMErrorKind(StrEnum):
    rate_limited = "rate_limited"
    spend_cap = "spend_cap"
    overloaded = "overloaded"
    server_error = "server_error"
    timeout = "timeout"
    connection = "connection"
    auth = "auth"
    permission = "permission"
    billing = "billing"
    model_not_found = "model_not_found"
    bad_request = "bad_request"
    context_too_long = "context_too_long"
    request_too_large = "request_too_large"
    refusal = "refusal"
    truncated = "truncated"
    invalid_output = "invalid_output"
    empty_output = "empty_output"
    circuit_open = "circuit_open"
    budget_exceeded = "budget_exceeded"
    deadline_exceeded = "deadline_exceeded"
    data_policy_blocked = "data_policy_blocked"
    disabled = "disabled"


class LLMError(Exception):
    def __init__(
        self,
        kind: LLMErrorKind,
        detail: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {detail}")
        self.kind = kind
        self.detail = detail
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_s = retry_after_s


class LLMUnavailable(LLMError):
    def __init__(self, attempts: list[LLMError]) -> None:
        last = attempts[-1] if attempts else None
        super().__init__(
            last.kind if last else LLMErrorKind.disabled,
            "every route failed" if attempts else "no route available",
        )
        self.attempts = attempts


class LLMBudgetExceeded(LLMError):
    def __init__(self, detail: str) -> None:
        super().__init__(LLMErrorKind.budget_exceeded, detail)


class LLMClient(Protocol):
    # never returns partial or unvalidated data; every caller owns a deterministic degrade path
    async def generate_structured[T: BaseModel](
        self,
        *,
        task: TaskId,
        schema: type[T],
        system: PromptRef,
        input: PromptInput,
        context: CallContext,
        tier: Tier | None = None,
        budget: CallBudget | None = None,
    ) -> LLMResult[T]: ...

    async def generate_text(
        self,
        *,
        task: TaskId,
        system: PromptRef,
        input: PromptInput,
        context: CallContext,
        tier: Tier | None = None,
        budget: CallBudget | None = None,
    ) -> LLMResult[str]: ...
