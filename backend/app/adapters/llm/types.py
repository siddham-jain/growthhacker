from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Effort, Tier
from app.ports.llm import TokenUsage

StopReason = Literal["end_turn", "max_tokens", "refusal", "content_filter", "stop_sequence", "other"]


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    cacheable: bool = False


@dataclass(frozen=True, slots=True)
class Turn:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True, slots=True)
class CompiledSchema:
    name: str
    schema: Mapping[str, Any]
    canonical_json: str


@dataclass(frozen=True, slots=True)
class Route:
    index: int
    provider: str
    model: str
    tier: Tier
    effort: Effort | None
    max_output_tokens: int
    timeout_s: float
    max_retries: int
    degraded: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    model: str
    system_blocks: tuple[TextBlock, ...]
    user_blocks: tuple[TextBlock, ...]
    followups: tuple[Turn, ...]
    output_schema: CompiledSchema | None
    max_output_tokens: int
    timeout_s: float
    effort: Effort | None
    metadata_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    stop_reason: StopReason
    usage: TokenUsage
    model: str
    request_id: str | None
    latency_ms: int
    raw_finish_reason: str
    refusal_category: str | None = None
