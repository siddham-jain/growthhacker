from datetime import timedelta

from sqlalchemy import select

from app.api import ids
from app.api.schemas.answer_citations import (
    AnswerCitationCounts,
    AnswerCitationsData,
    ChannelCitations,
    CitedVideo,
    QuestionCitations,
)
from app.api.schemas.results import VideoSummary
from app.api.services import api_service_registry
from app.api.services_impl.common import ServiceBase, summary_or_bare, video_summaries
from app.core.tenancy import TenantScope
from app.models.competitors import CompetitorSet
from app.services.answer_citations.lens import OWNER_ORDER, owner_of, question_status
from app.services.answer_citations.store import AnswerCitationStore, StoredCheck

ANSWER_CITATIONS = "answer_citations"


class AnswerCitationReadService(ServiceBase):
    """conversion-lens view of web answer citations: which buyer questions cite you, competitors, or neither."""

    async def view(self, scope: TenantScope) -> AnswerCitationsData:
        client = await self.client(scope)
        own = client.own_channel_id if client.own_channel_status == "verified" else None
        competitors = set(await self._competitor_ids(scope))
        window_days = self.settings.policy.retention_days
        checks = await AnswerCitationStore(self.session_factory, scope).latest(
            self.clock.now() - timedelta(days=window_days)
        )
        summaries = await video_summaries(
            self.session_factory, [citation.video_id for check in checks for citation in check.citations]
        )
        questions = [self._question(check, own, competitors, summaries) for check in checks]
        return AnswerCitationsData(
            window_days=window_days,
            own_channel_id=own,
            last_checked_at=max((check.checked_at for check in checks), default=None),
            counts=_counts(questions),
            questions=questions,
            competitors=_by_competitor(questions),
        )

    @staticmethod
    def _question(
        check: StoredCheck, own: str | None, competitors: set[str], summaries: dict[str, VideoSummary]
    ) -> QuestionCitations:
        videos = sorted(
            (
                CitedVideo(
                    video=summary_or_bare(summaries, citation.video_id, citation.channel_id),
                    owner=owner_of(citation.channel_id, own, competitors),
                    position=citation.position,
                    cited_in=citation.cited_in,  # type: ignore[arg-type]
                )
                for citation in check.citations
            ),
            key=lambda video: (OWNER_ORDER[video.owner], video.position or 99, video.video.video_id),
        )
        return QuestionCitations(
            buyer_question_id=ids.encode(ids.BUYER_QUESTION, check.question_id),
            text=check.text,
            funnel_stage=check.funnel_stage,
            checked_at=check.checked_at,
            status=question_status(video.owner for video in videos),
            youtube_urls=check.youtube_url_count,
            videos=videos,
        )

    async def _competitor_ids(self, scope: TenantScope) -> list[str]:
        statement = (
            select(CompetitorSet.competitor_channel_ids)
            .where(
                CompetitorSet.workspace_id == scope.workspace_id, CompetitorSet.client_id == scope.client_id
            )
            .order_by(CompetitorSet.version.desc())
            .limit(1)
        )
        async with self.session_factory() as session:
            return list((await session.execute(statement)).scalar_one_or_none() or [])


def _counts(questions: list[QuestionCitations]) -> AnswerCitationCounts:
    statuses = [question.status for question in questions]
    return AnswerCitationCounts(
        questions_checked=len(questions),
        cite_you=statuses.count("cites_you"),
        cite_competitors=sum(
            1 for question in questions if any(video.owner == "competitor" for video in question.videos)
        ),
        competitor_only=statuses.count("competitor_only"),
        no_tracked_channel=statuses.count("others_only") + statuses.count("no_youtube"),
    )


def _by_competitor(questions: list[QuestionCitations]) -> list[ChannelCitations]:
    # alphabetical by channel title so the list never reads as a ranking
    channels: dict[str, ChannelCitations] = {}
    for question in questions:
        for video in question.videos:
            if video.owner != "competitor":
                continue
            entry = channels.setdefault(
                video.video.channel_id,
                ChannelCitations(
                    channel_id=video.video.channel_id,
                    channel_title=video.video.channel_title,
                    buyer_question_ids=[],
                ),
            )
            if question.buyer_question_id not in entry.buyer_question_ids:
                entry.buyer_question_ids.append(question.buyer_question_id)
    return sorted(
        channels.values(),
        key=lambda entry: ((entry.channel_title or entry.channel_id).casefold(), entry.channel_id),
    )


api_service_registry.add(ANSWER_CITATIONS, AnswerCitationReadService)
