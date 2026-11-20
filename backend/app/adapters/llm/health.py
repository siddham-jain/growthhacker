from dataclasses import dataclass
from typing import Literal

import anthropic
import openai

from app.adapters.llm.capabilities import anthropic_capabilities
from app.core.config import TIERS, LLMSettings

HealthStatus = Literal["ok", "degraded", "failed"]


@dataclass(frozen=True, slots=True)
class RouteHealth:
    provider: str
    model: str
    status: HealthStatus
    detail: str


async def _anthropic_model(client: anthropic.AsyncAnthropic, model: str) -> RouteHealth:
    try:
        info = await client.models.retrieve(model)
    except anthropic.NotFoundError:
        return RouteHealth("anthropic", model, "failed", "model id invalid or retired")
    except anthropic.AuthenticationError:
        return RouteHealth("anthropic", model, "failed", "api key rejected")
    except anthropic.APIError as exc:
        return RouteHealth("anthropic", model, "degraded", f"models api error: {type(exc).__name__}")
    problems = []
    if info.lifecycle != "active":
        problems.append(f"lifecycle {info.lifecycle}")
    capabilities = info.capabilities
    expected = anthropic_capabilities(model)
    if capabilities is not None:
        if not capabilities.structured_outputs.supported:
            problems.append("no structured outputs")
        if bool(expected.effort_levels) != capabilities.effort.supported:
            problems.append("effort support differs from the capability table")
    return RouteHealth("anthropic", model, "degraded" if problems else "ok", ", ".join(problems) or "ready")


async def check_llm_health(settings: LLMSettings) -> list[RouteHealth]:
    """startup/readiness check: key validity, model availability and capability drift per configured route."""
    report: list[RouteHealth] = []
    anthropic_settings = settings.anthropic
    if anthropic_settings.configured and anthropic_settings.api_key is not None:
        models = sorted({m for tier in TIERS if (m := anthropic_settings.tier(tier).model)})
        async with anthropic.AsyncAnthropic(
            api_key=anthropic_settings.api_key.get_secret_value(),
            base_url=anthropic_settings.base_url,
            max_retries=0,
            timeout=10,
        ) as client:
            for model in models:
                report.append(await _anthropic_model(client, model))
    compat = settings.openai_compat
    if compat.configured and compat.api_key is not None:
        async with openai.AsyncOpenAI(
            api_key=compat.api_key.get_secret_value(), base_url=compat.base_url, max_retries=0, timeout=10
        ) as client:
            try:
                await client.models.list()
                status: HealthStatus = "ok"
                detail = "reachable"
            except openai.APIError as exc:
                status, detail = "failed", f"gateway error: {type(exc).__name__}"
        for tier in TIERS:
            model = compat.tier(tier).model
            if model:
                report.append(RouteHealth("openai_compat", model, status, detail))
    return report
