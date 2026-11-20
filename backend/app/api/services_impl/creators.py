from uuid import UUID

from app.api.pagination import PageRequest
from app.api.schemas.results import CreatorsData, CreatorView
from app.api.services import CREATORS, api_service_registry
from app.api.services_impl.common import ServiceBase, channel_refs, sort_items
from app.api.services_impl.recommendations import RecommendationReadService
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.recommendations import CreatorSubject, Recommendation, RecType
from app.services.recommend.creators.ranker import CREATOR_SORTS, relevance_group, sort_value


def ordered(cards: list[Recommendation], criterion: str, strong_share: float) -> list[Recommendation]:
    # strong relevance first, then the single chosen raw metric, highest first; ties by channel id
    def subject(card: Recommendation) -> CreatorSubject:
        assert isinstance(card.extras.subject, CreatorSubject)
        return card.extras.subject

    by_channel = sorted(cards, key=lambda card: subject(card).channel_id)
    by_value = sort_items(by_channel, "-value", {"value": lambda card: sort_value(subject(card), criterion)})  # type: ignore[arg-type]
    return sorted(by_value, key=lambda card: relevance_group(subject(card), strong_share) != "strong")


class CreatorReadService(ServiceBase):
    """07's creator cards, grouped by relevance and ordered by one transparent criterion; no score."""

    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.recommendations = RecommendationReadService(container)

    async def list_creators(
        self, scope: TenantScope, run_id: UUID, window_days: int, sort: str | None, page: PageRequest
    ) -> CreatorsData:
        window = await self.recommendations.window_output(scope, run_id, window_days)
        criterion = sort or window.creator_list.sort
        strong_share = self.settings.recommend.creators.strong_match_share
        cards = await self.recommendations.worded(
            scope,
            run_id,
            [
                card
                for card in window.set.recommendations
                if card.type is RecType.creator and isinstance(card.extras.subject, CreatorSubject)
            ],
        )
        refs = await channel_refs(self.session_factory, [card.extras.subject.channel_id for card in cards])  # type: ignore[union-attr]
        views = []
        for position, card in enumerate(ordered(cards, criterion, strong_share), 1):
            subject = card.extras.subject
            assert isinstance(subject, CreatorSubject)
            views.append(
                CreatorView(
                    channel=refs[subject.channel_id].model_copy(
                        update={"title": subject.title, "handle": subject.handle}
                    ),
                    group=relevance_group(subject, strong_share),
                    position=position,
                    sort=criterion,
                    sort_value=sort_value(subject, criterion),  # type: ignore[arg-type]
                    subscriber_band=subject.subscriber_band.value,
                    uploads_90d=subject.uploads_90d,
                    match_share=subject.relevance.match_share,
                    topics_covered=subject.relevance.topics_covered,
                    performance=subject.performance,
                    consistency=subject.consistency,
                    collaborations=subject.collaborations,
                    reference_videos=card.reference_videos,
                    recommendation_id=card.id,
                    summary=card.summary,
                )
            )
        chunk = self.paginate(views, page, {"run": str(run_id), "window": window_days, "sort": criterion})
        return CreatorsData(
            sort=criterion,
            available_sorts=list(CREATOR_SORTS),
            items=chunk.items,
            next_cursor=chunk.next_cursor,
            limit=chunk.limit,
        )


@api_service_registry.register(CREATORS)
def build_creator_service(container: Container) -> CreatorReadService:
    return CreatorReadService(container)
