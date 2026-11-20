from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.config import RecommendSettings
from app.domain.recommendations import RecommendationDraft
from app.pipeline.inputs.common import ProfileMissing, active_run, analysis_request_of, profile_and_questions
from app.pipeline.stages.analytics import ANALYTICS_RESULT
from app.pipeline.stages.news import NEWS_ARTIFACT
from app.pipeline.stages.niche import NICHE_DISCOVERY
from app.pipeline.stages.refresh import REFRESH_ARTIFACT
from app.pipeline.stages.youtube import INGEST
from app.services.analytics.inputs import AnalyticsInputSource
from app.services.analytics.result import AnalyticsResult
from app.services.competitors.repository import CompetitorRepository
from app.services.recommend.feedback import feedback_state
from app.services.recommend.inputs import ClientFacts, RecommendInputs, WindowAnalytics
from app.services.recommend.repository import RecommendationRepository


def window_analytics(result: AnalyticsResult) -> list[WindowAnalytics]:
    """06's per-window blocks in 07's shape; lifts from every format segment go into one list."""
    windows = []
    for window_days, block in sorted(result.windows.items()):
        windows.append(
            WindowAnalytics(
                window_days=window_days,
                # 06 keys metrics by comparison age (day 3 for the 7-day window, day 7 for 30/90)
                metrics=dict(result.metrics.get(block.comparison_age_days, {})),
                traits=dict(result.traits),
                lifts=[lift for _, lifts in sorted(block.lifts.items()) for lift in lifts],
                gap_matrix=block.gap_matrix,
                own_report=block.own_report,
            )
        )
    return windows


class RunRecommendInputs:
    """07's inputs for the client's running analysis: this run's artifacts plus 03/04/05/07 tables."""

    def __init__(self, container: Any, analytics_inputs: AnalyticsInputSource, settings: RecommendSettings):
        self._container = container
        self._analytics_inputs = analytics_inputs
        self._settings = settings

    async def load(self, client_id: UUID, as_of: datetime, window_days: int) -> RecommendInputs:
        run = await active_run(self._container, client_id)
        snapshot, scope = run.snapshot, run.scope
        profile, questions = await profile_and_questions(self._container, scope)
        if profile is None:
            raise ProfileMissing("the client has no business profile; finish onboarding first")
        analytics = await run.artifacts.get(ANALYTICS_RESULT)
        news = await run.artifacts.get_optional(NEWS_ARTIFACT)
        refresh = await run.artifacts.get_optional(REFRESH_ARTIFACT)
        niche = await run.artifacts.get_optional(NICHE_DISCOVERY)
        ingest = await run.artifacts.get_optional(INGEST)
        inputs = await self._analytics_inputs.load(analysis_request_of(snapshot, as_of))
        async with self._container.uow(read_only=True) as uow:
            competitors = CompetitorRepository(uow.session, scope)
            competitor_set = await competitors.active_set()
            candidates = await competitors.load_candidates()
            recommendations = uow.repository(RecommendationRepository, scope)
            config = await recommendations.config()
            feedback = feedback_state(await recommendations.feedback_rows(), as_of, self._settings.feedback)
            previous = {}
            for window in snapshot.windows_computed:
                previous |= await recommendations.previous_cards(window)
        news_drafts: list[RecommendationDraft] = news.drafts if news is not None else []
        return RecommendInputs(
            client_id=client_id,
            as_of=as_of,
            selected_window=window_days,
            client=ClientFacts.from_profile(profile),
            buyer_questions=questions,
            topic_refs=snapshot.topic_refs,
            topic_matches=inputs.topic_matches,
            directness=inputs.directness,
            own_channel_id=snapshot.own_channel_id,
            competitor_set=competitor_set,
            competitor_candidates=candidates,
            channels=[scoped.channel for scoped in inputs.channels],
            channel_roles={scoped.channel.channel_id: scoped.role for scoped in inputs.channels},
            videos=inputs.videos,
            niche_search_hits=dict(niche.search_hits) if niche is not None else {},
            analytics=window_analytics(analytics),
            news_drafts=news_drafts,
            refresh_drafts=refresh.recommendation_drafts if refresh is not None else [],
            refreshes=refresh.refreshes if refresh is not None else [],
            config=config,
            feedback=feedback,
            previous_cards=previous,
            data_refreshed_at=ingest.data_as_of if ingest is not None else None,
        )
