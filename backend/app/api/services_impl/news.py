from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update

from app.api import ids
from app.api.errors import NotFound
from app.api.pagination import Page, PageRequest
from app.api.schemas.results import DemandCheckView, EarlySignalView, NewsOpportunityView, NewsRef
from app.api.services import NEWS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.api.services_impl.recommendations import RecommendationReadService, with_copy
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.news import NewsSignal
from app.domain.recommendations import NewsSubject, Recommendation, RecType
from app.models.news import NewsSignal as NewsSignalRow
from app.models.recommendations import Recommendation as RecommendationRow
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.services.news.lifecycle import card_ids
from app.services.news.repository import NewsSignalStore

CHECKED = frozenset({"checked", "local_only"})
VIEW_STATUS = {"dismissed": "dismissed", "used": "used"}


def opportunity_view(signal: NewsSignal, card: Recommendation | None) -> NewsOpportunityView:
    source = signal.sources[0]
    demand = signal.demand
    checked = demand.status in CHECKED
    subject = card.extras.subject if card is not None else None
    news = card.extras.news if card is not None else None
    return NewsOpportunityView(
        id=ids.encode(ids.NEWS, signal.id),
        news=NewsRef(
            news_id=ids.encode(ids.NEWS, signal.id),
            headline=source.title or signal.judgement.angle,
            url=source.url,
            source_domain=(urlsplit(source.url).hostname or source.publisher).removeprefix("www."),
            published_at=source.published_at,
        ),
        recommendation_id=card.id if card is not None else None,
        summary=card.summary if card is not None else None,
        relevance=signal.relevance_score,
        offering_refs=[link.offering_id for link in signal.judgement.offering_links],
        demand_check=DemandCheckView(
            status=demand.status,
            youtube_results_7d=demand.early_video_count if checked else None,
            outliers=demand.outlier_count if checked else None,
        ),
        urgency=subject.urgency if isinstance(subject, NewsSubject) else signal.urgency,
        expires_at=subject.expires_at if isinstance(subject, NewsSubject) else signal.expires_at,
        reference_count=len(card.reference_videos) if card is not None else 0,
        review_required=news.compliance.review_required
        if news is not None
        else signal.safety.decision == "needs_review",
        status=VIEW_STATUS.get(signal.status, "new"),  # type: ignore[arg-type]
    )


class NewsReadService(ServiceBase):
    """08's signals joined to 07's published news cards; first-mover items stay on their own surface."""

    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.recommendations = RecommendationReadService(container)

    async def _signals(self, scope: TenantScope) -> dict[UUID, NewsSignal]:
        since = self.clock.now() - timedelta(days=self.settings.policy.retention_days)
        async with self.session_factory() as session:
            signals = await NewsSignalStore(session, scope, self.settings.policy.retention_days).live_signals(
                since
            )
        return {signal.id: signal for signal in signals}

    async def opportunities(
        self, scope: TenantScope, run_id: UUID, status: str | None, page: PageRequest
    ) -> Page[NewsOpportunityView]:
        output = await self.artifact(scope, run_id, RECOMMEND_ARTIFACT)
        window = output.window(output.selected_window)
        copies = await self.recommendations.copies(scope, run_id)
        cards = [
            with_copy(card, copies.get(card.extras.stable_key))
            for card in (window.set.recommendations if window else [])
            if card.type is RecType.news_opportunity and isinstance(card.extras.subject, NewsSubject)
        ]
        signals = await self._signals(scope)
        views = [
            opportunity_view(signals[subject.news_signal_id], card)
            for card in cards
            if isinstance(subject := card.extras.subject, NewsSubject) and subject.news_signal_id in signals
        ]
        if status is not None:
            views = [view for view in views if view.status == status]
        return self.paginate(views, page, {"run": str(run_id), "status": status})

    async def signals(self, scope: TenantScope, page: PageRequest) -> Page[NewsSignal]:
        signals = list((await self._signals(scope)).values())
        return self.paginate(signals, page, {"client": str(scope.client_id)})

    async def early_signals(
        self, scope: TenantScope, run_id: UUID, window_days: int
    ) -> list[EarlySignalView]:
        window = await self.recommendations.window_output(scope, run_id, window_days)
        signals = await self._signals(scope)
        return [
            EarlySignalView(
                id=item.id,
                opportunity_id=ids.encode(ids.NEWS, item.news_signal_id),
                key_entity=item.key_entity,
                angle=item.angle,
                urgency=item.urgency,
                expires_at=item.expires_at,
                sources=item.sources,
                reference_videos=item.reference_videos,
                confidence=item.confidence,
                statements=item.statements,
                window_days=item.window_days,
            )
            for item in window.early_signals
            if item.news_signal_id in signals and signals[item.news_signal_id].status != "dismissed"
        ]

    async def dismiss(self, scope: TenantScope, opportunity_id: str) -> NewsOpportunityView:
        signal_id = ids.decode(ids.NEWS, opportunity_id)
        async with self.session_factory() as session, session.begin():
            client_id = (
                await session.execute(
                    update(NewsSignalRow)
                    .where(NewsSignalRow.id == signal_id, NewsSignalRow.workspace_id == scope.workspace_id)
                    .values(status="dismissed", updated_at=self.clock.now())
                    .returning(NewsSignalRow.client_id)
                )
            ).scalar_one_or_none()
        if client_id is None:
            raise NotFound("news opportunity not found")
        client_scope = scope.for_client(client_id)
        signal = (await self._signals(client_scope)).get(signal_id)
        if signal is None:
            raise NotFound("this news story was withdrawn")
        return opportunity_view(signal, await self._card(client_scope, signal_id))

    async def _card(self, scope: TenantScope, signal_id: UUID) -> Recommendation | None:
        assert scope.client_id is not None
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(RecommendationRow)
                    .where(
                        RecommendationRow.workspace_id == scope.workspace_id,
                        RecommendationRow.client_id == scope.client_id,
                        RecommendationRow.rec_id.in_(card_ids(scope.client_id, signal_id)),
                    )
                    .order_by(RecommendationRow.generated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        try:
            return Recommendation.model_validate(row.payload)
        except ValidationError:
            return None


@api_service_registry.register(NEWS)
def build_news_service(container: Container) -> NewsReadService:
    return NewsReadService(container)
