from dataclasses import dataclass
from typing import Literal

from app.core.config import Effort
from app.core.registry import Registry

ThinkingMode = Literal["none", "adaptive"]
EFFORT_ORDER: tuple[Effort, ...] = ("low", "medium", "high")


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    effort_levels: frozenset[Effort]
    thinking: ThinkingMode
    context_window: int
    max_output_tokens: int


model_capabilities: Registry[ModelCapabilities] = Registry("llm_model_capabilities")

model_capabilities.add(
    "claude-haiku-4-5",
    ModelCapabilities(
        effort_levels=frozenset(),
        thinking="none",
        context_window=200_000,
        max_output_tokens=64_000,
    ),
)
model_capabilities.add(
    "claude-sonnet-5-5",
    ModelCapabilities(
        effort_levels=frozenset(EFFORT_ORDER),
        thinking="adaptive",
        context_window=1_000_000,
        max_output_tokens=128_000,
    ),
)
model_capabilities.add(
    "claude-haiku-5-5",
    ModelCapabilities(
        effort_levels=frozenset(EFFORT_ORDER),
        thinking="adaptive",
        context_window=1_000_000,
        max_output_tokens=64_000,
    ),
)

# unknown anthropic ids get the most restrictive parameter set
CONSERVATIVE_ANTHROPIC = ModelCapabilities(
    effort_levels=frozenset(),
    thinking="none",
    context_window=200_000,
    max_output_tokens=8192,
)


def anthropic_capabilities(model: str) -> ModelCapabilities:
    # resolved ids carry a date suffix, e.g. claude-haiku-4-5-20251001
    for known in sorted(model_capabilities.keys(), key=len, reverse=True):
        if model == known or model.startswith(f"{known}-"):
            return model_capabilities.get(known)
    return CONSERVATIVE_ANTHROPIC


def openai_compat_capabilities(
    *, supports_reasoning_effort: bool, context_window: int, max_output_tokens: int
) -> ModelCapabilities:
    return ModelCapabilities(
        effort_levels=frozenset(EFFORT_ORDER) if supports_reasoning_effort else frozenset(),
        thinking="none",
        context_window=context_window,
        max_output_tokens=max_output_tokens,
    )


def supported_effort(capabilities: ModelCapabilities, requested: Effort | None) -> Effort | None:
    if requested is None or requested not in capabilities.effort_levels:
        return None
    return requested


def lower_effort(effort: Effort | None) -> Effort | None:
    if effort is None:
        return None
    position = EFFORT_ORDER.index(effort)
    return EFFORT_ORDER[max(0, position - 1)]


def sized_max_tokens(capabilities: ModelCapabilities, requested: int, expected_reply_tokens: int) -> int:
    # adaptive thinking shares max_tokens with the reply, so leave room for both
    floor = 2 * expected_reply_tokens if capabilities.thinking == "adaptive" else expected_reply_tokens
    return min(max(requested, floor), capabilities.max_output_tokens)
