import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.huggingface import HF_MODELS_API, HuggingFaceTrendingSource
from tests.news.adapters.conftest import SINCE, planned


def model(repo: str, score: int, created: str = "2026-11-18T10:00:00.000Z") -> dict:
    return {"id": repo, "trendingScore": score, "likes": 10, "downloads": 5, "tags": [], "createdAt": created}


@respx.mock
async def test_keeps_watched_orgs_and_top_trending(deps: FreshnessDeps) -> None:
    models = [model(f"someone/model-{index}", index) for index in range(20)]
    models += [
        model("meta-llama/Llama-5-8B", 3),
        model("Qwen/Qwen4-old", 2, created="2026-01-01T00:00:00.000Z"),
        model("hot/viral-model", 500),
    ]
    respx.get(HF_MODELS_API).mock(return_value=httpx.Response(200, json=models))
    result = await HuggingFaceTrendingSource(deps).fetch(
        planned("hf_trending", "trending", orgs=["Meta-Llama", "qwen"]), SINCE
    )
    repos = [item.external_ids["hf_repo"] for item in result.items]
    assert "meta-llama/Llama-5-8B" in repos
    assert "hot/viral-model" in repos
    assert "Qwen/Qwen4-old" not in repos
    assert "someone/model-3" not in repos
    assert result.items[0].url.startswith("https://huggingface.co/")


@respx.mock
async def test_hub_outage(deps: FreshnessDeps) -> None:
    respx.get(HF_MODELS_API).mock(return_value=httpx.Response(500))
    assert (await HuggingFaceTrendingSource(deps).fetch(planned("hf_trending", "t"), SINCE)).status == "error"
