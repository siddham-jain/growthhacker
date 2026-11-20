import inspect

import pytest

import app.adapters.freshness  # noqa: F401
from app.adapters.freshness.base import FreshnessDeps, build_source
from app.core.registry import freshness_source_registry
from app.ports.freshness import SourceCapabilities

EXPECTED_KEYS = {
    "tavily_news",
    "tavily_domains",
    "pubmed",
    "rss",
    "arxiv",
    "hf_trending",
    "who_don",
    "openfda",
    "gdelt",
}


def test_every_planned_source_is_registered() -> None:
    assert set(freshness_source_registry.keys()) >= EXPECTED_KEYS


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
async def test_source_satisfies_protocol(deps: FreshnessDeps, key: str) -> None:
    source = build_source(key, deps)
    assert source.key == key
    assert isinstance(source.capabilities, SourceCapabilities)
    assert inspect.iscoroutinefunction(source.fetch)
    health = await source.health()
    assert health.checked_at == deps.clock.now()
