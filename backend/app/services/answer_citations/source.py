from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.adapters.search.errors import WebSearchError
from app.core.logging import get_logger
from app.domain.youtube import VideoRecord
from app.ports.clock import Clock
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.answer_citations.models import (
    AnswerSearch,
    AnswerSearchResult,
    CitationCheck,
    QuestionRef,
    ResolvedCitation,
)
from app.services.answer_citations.urls import cited_videos

VIDEOS_PER_CALL = 50

logger = get_logger(__name__)


@dataclass(frozen=True)
class SearchedQuestion:
    question: QuestionRef
    result: AnswerSearchResult


class AnswerCitationSource:
    """runs a web answer search per buyer question and resolves the cited youtube videos via videos.list."""

    source_id = "web_answer_citations"

    def __init__(
        self, search: AnswerSearch, gateway: YouTubeGateway, clock: Clock, *, max_results: int
    ) -> None:
        self._search = search
        self._gateway = gateway
        self._clock = clock
        self._max_results = max_results

    async def check(
        self,
        questions: Sequence[QuestionRef],
        ctx: RequestContext,
        *,
        country: str | None,
        credit_budget: float,
    ) -> list[CitationCheck]:
        searched = await self._searched(questions, ctx, country, credit_budget)
        wanted = list(
            dict.fromkeys(cited.video_id for item in searched for cited in cited_videos(item.result))
        )
        videos = await self._resolve(wanted, ctx)
        checked_at = self._clock.now()
        return [self._as_check(item, videos, checked_at) for item in searched]

    async def _searched(
        self, questions: Sequence[QuestionRef], ctx: RequestContext, country: str | None, credit_budget: float
    ) -> list[SearchedQuestion]:
        searched: list[SearchedQuestion] = []
        spent = 0.0
        for question in questions:
            # a basic search costs one credit; stop before a call that could cross the budget
            if spent + 1 > credit_budget:
                break
            try:
                result = await self._search.answer(
                    question.text, max_results=self._max_results, country=country, client_id=ctx.client_id
                )
            except WebSearchError as error:
                logger.warning(
                    "answer_citations.search_failed", kind=error.kind, client_id=str(ctx.client_id)
                )
                if error.disables_provider:
                    break
                continue
            spent += result.credits_used
            searched.append(SearchedQuestion(question=question, result=result))
        return searched

    async def _resolve(self, video_ids: list[str], ctx: RequestContext) -> dict[str, VideoRecord]:
        found: dict[str, VideoRecord] = {}
        for start in range(0, len(video_ids), VIDEOS_PER_CALL):
            batch = await self._gateway.videos_by_id(video_ids[start : start + VIDEOS_PER_CALL], ctx)
            found.update({video.video_id: video for video in batch.found})
        return found

    @staticmethod
    def _as_check(
        item: SearchedQuestion, videos: dict[str, VideoRecord], checked_at: datetime
    ) -> CitationCheck:
        cited = cited_videos(item.result)
        citations = [
            ResolvedCitation(
                video_id=entry.video_id,
                channel_id=videos[entry.video_id].channel_id,
                cited_url=entry.url,
                position=entry.position,
                cited_in=entry.cited_in,
            )
            for entry in cited
            if entry.video_id in videos and videos[entry.video_id].privacy_status == "public"
        ]
        return CitationCheck(
            question_id=item.question.id,
            checked_at=checked_at,
            result_count=len(item.result.sources),
            youtube_url_count=len(cited),
            unresolved_count=len(cited) - len(citations),
            answer_returned=bool(item.result.answer),
            credits=item.result.credits_used,
            from_cache=item.result.from_cache,
            citations=citations,
        )
