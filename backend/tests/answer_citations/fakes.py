from uuid import UUID

from app.services.answer_citations.models import AnswerSearchResult, AnswerSource

OWN = "UCownChannel00000000000A"
RIVAL = "UCrivalChannel000000000B"
RIVAL_TWO = "UCrivalChannelTwo000000C"
OTHER = "UCotherChannel000000000D"


def result(*urls: str, answer: str | None = "an answer", from_cache: bool = False) -> AnswerSearchResult:
    return AnswerSearchResult(
        answer=answer,
        sources=[AnswerSource(url=url, position=rank) for rank, url in enumerate(urls, start=1)],
        credits_used=0.0 if from_cache else 1.0,
        from_cache=from_cache,
    )


class FakeAnswerSearch:
    def __init__(self, answers: dict[str, AnswerSearchResult | Exception] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, int, str | None, UUID | None]] = []

    async def answer(
        self, question: str, *, max_results: int, country: str | None, client_id: UUID | None
    ) -> AnswerSearchResult:
        self.calls.append((question, max_results, country, client_id))
        found = self.answers.get(question, result())
        if isinstance(found, Exception):
            raise found
        return found
