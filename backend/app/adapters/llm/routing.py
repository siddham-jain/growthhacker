from app.adapters.llm.types import Route
from app.core.config import LLMRoute, LLMSettings, Tier
from app.services.llm.tasks import TaskSpec

TIER_RANK: dict[Tier, int] = {"low": 0, "medium": 1, "high": 2}


class TaskRouter:
    """task -> ordered route chain: the tier's provider routes, then configured cross-tier stand-ins."""

    def __init__(self, settings: LLMSettings) -> None:
        self._settings = settings

    def resolve(self, task: TaskSpec, tier: Tier | None = None) -> list[Route]:
        chosen = tier or task.tier
        candidates: list[tuple[LLMRoute, Tier]] = [(route, chosen) for route in self._settings.routes(chosen)]
        for stand_in in self._settings.resilience.cross_tier_fallback.get(chosen, []):
            candidates.extend((route, stand_in) for route in self._settings.routes(stand_in))
        routes: list[Route] = []
        seen: set[tuple[str, str, str | None]] = set()
        for configured, route_tier in candidates:
            settings = configured.tier
            assert settings.model is not None
            identity = (configured.provider_name, settings.model, settings.effort)
            if identity in seen:
                continue
            seen.add(identity)
            routes.append(
                Route(
                    index=len(routes),
                    provider=configured.provider_name,
                    model=settings.model,
                    tier=route_tier,
                    effort=settings.effort,
                    max_output_tokens=task.max_output_tokens,
                    timeout_s=task.timeout_s,
                    max_retries=settings.max_retries,
                    degraded=TIER_RANK[route_tier] < TIER_RANK[chosen],
                )
            )
        return routes
