import uuid
from typing import Any

import pytest
from tavily.errors import UsageLimitExceededError

from app.adapters.search.errors import WebSearchError
from app.adapters.search.ledger import CreditCharge
from app.adapters.search.tavily_answers import TavilyAnswerSearch


class FakeTavily:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((query, kwargs))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class MemoryCache:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self.items.get(key)

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        self.items[key] = value


class Recorder:
    def __init__(self) -> None:
        self.charges: list[tuple[CreditCharge, uuid.UUID | None]] = []

    async def record(
        self, charge: CreditCharge, *, client_id: uuid.UUID | None, run_id: uuid.UUID | None
    ) -> None:
        self.charges.append((charge, client_id))


PAYLOAD = {
    "answer": "Recovery takes about six weeks.",
    "results": [
        {"url": "https://www.apollohospitals.com/knee", "title": "Knee"},
        {"title": "no url"},
        {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Video"},
    ],
    "usage": {"credits": 1},
}


async def test_requests_a_basic_answer_and_keeps_result_rank() -> None:
    api, recorder, client_id = FakeTavily(PAYLOAD), Recorder(), uuid.uuid4()
    search = TavilyAnswerSearch(api, recorder=recorder)

    found = await search.answer("knee recovery time", max_results=8, country="india", client_id=client_id)

    [(query, params)] = api.calls
    assert query == "knee recovery time"
    assert params["include_answer"] == "basic" and params["search_depth"] == "basic"
    assert params["max_results"] == 8 and params["country"] == "india"
    assert [(source.url, source.position) for source in found.sources] == [
        ("https://www.apollohospitals.com/knee", 1),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", 3),
    ]
    assert found.answer == PAYLOAD["answer"] and found.credits_used == 1.0 and not found.from_cache
    assert [(charge.operation, charge.credits, owner) for charge, owner in recorder.charges] == [
        ("answer_search", 1.0, client_id)
    ]


async def test_a_cached_answer_costs_nothing() -> None:
    api, cache = FakeTavily(PAYLOAD), MemoryCache()
    search = TavilyAnswerSearch(api, cache=cache, cache_ttl_s=60)

    await search.answer("q", max_results=10, country=None, client_id=None)
    again = await search.answer("q", max_results=10, country=None, client_id=None)

    assert len(api.calls) == 1
    assert again.from_cache and again.credits_used == 0.0
    assert search.meter.spent == 1.0 and search.meter.calls == 2


async def test_blank_answers_are_none_and_errors_are_classified() -> None:
    blank = await TavilyAnswerSearch(FakeTavily({"answer": "  ", "results": []})).answer(
        "q", max_results=5, country=None, client_id=None
    )
    assert blank.answer is None and blank.sources == [] and blank.credits_used == 1.0

    recorder = Recorder()
    failing = TavilyAnswerSearch(FakeTavily(UsageLimitExceededError("limit")), recorder=recorder)
    with pytest.raises(WebSearchError) as raised:
        await failing.answer("q", max_results=5, country=None, client_id=None)
    assert raised.value.kind == "credit_limit" and raised.value.disables_provider
    assert recorder.charges[0][0].outcome == "error"
