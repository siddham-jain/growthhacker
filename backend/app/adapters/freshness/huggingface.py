from datetime import datetime
from typing import Any, ClassVar

import httpx
import numpy as np

from app.adapters.freshness.base import FreshnessDeps, failed_result, get_json, parse_iso, respect_interval
from app.core.registry import freshness_source_registry
from app.ports.freshness import (
    NewsFetchResult,
    PlannedQuery,
    RawNewsItem,
    SourceCapabilities,
    SourceHealth,
)

HF_MODELS_API = "https://huggingface.co/api/models"
TRENDING_PERCENTILE = 95
LIMIT = 50


@freshness_source_registry.register("hf_trending")
class HuggingFaceTrendingSource:
    key: ClassVar[str] = "hf_trending"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_time_filter=False,
        supports_language=False,
        supports_region=False,
        returns_dates="datetime",
        cost_unit="free",
        min_interval_seconds=1.0,
        content_policy="metadata_and_link_only",
    )

    def __init__(self, deps: FreshnessDeps) -> None:
        self.deps = deps

    async def fetch(self, query: PlannedQuery, since: datetime) -> NewsFetchResult:
        await respect_interval(self.deps, self.key, self.capabilities.min_interval_seconds)
        try:
            response = await get_json(self.deps, HF_MODELS_API, {"sort": "trendingScore", "limit": LIMIT})
            if response.status_code != 200:
                return failed_result(response.status_code, f"huggingface http {response.status_code}")
            models: list[dict[str, Any]] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return failed_result(None, f"huggingface request failed: {type(error).__name__}")
        orgs = {org.lower() for org in query.params.get("orgs", [])}
        scores = [float(model.get("trendingScore") or 0) for model in models]
        threshold = float(np.percentile(scores, TRENDING_PERCENTILE)) if scores else float("inf")
        items = []
        for model, score in zip(models, scores, strict=True):
            repo = model.get("id") or model.get("modelId")
            if not repo:
                continue
            org = repo.split("/", 1)[0].lower() if "/" in repo else ""
            if org not in orgs and score < threshold:
                continue
            published_at = parse_iso(model.get("createdAt")) or parse_iso(model.get("lastModified"))
            if published_at is not None and published_at < since:
                continue
            items.append(
                RawNewsItem(
                    url=f"https://huggingface.co/{repo}",
                    title=repo,
                    snippet="",
                    published_at=published_at,
                    provider_score=score,
                    external_ids={"hf_repo": repo},
                )
            )
        return NewsFetchResult(items=items, cost=0.0, status="ok")

    async def health(self) -> SourceHealth:
        return SourceHealth(healthy=True, checked_at=self.deps.clock.now())
