from collections.abc import Mapping, Sequence

from app.domain.card_copy import CardCopy
from app.services.llm.writer.schemas import WriterClientContext
from app.services.llm.writer.service import CardWritingService
from app.services.recommend.writer import CardRequest


class CardWritingServiceWriter:
    """the recommend writer port over 10's card writing service (writer, critic, one revision)."""

    def __init__(self, service: CardWritingService) -> None:
        self.service = service

    async def write(self, requests: Sequence[CardRequest]) -> Mapping[str, CardCopy]:
        if not requests:
            return {}
        first = requests[0]
        client, facts = first.client, first.facts
        context = WriterClientContext(
            name=client.name,
            vertical=client.vertical or "business",
            language=client.language,
            offerings=client.offerings,
            value_props=client.value_propositions,
            buyer_questions=facts.buyer_questions,
            locations=client.locations,
            domains=client.website_domains,
            recent_titles=list(facts.recent_titles),
            known_channel_names=list(facts.known_channel_names),
            topic_labels=facts.topic_labels,
        )
        written = await self.service.write(
            [request.draft for request in requests],
            context,
            run_id=first.run_id,
            client_id=first.draft.client_id,
            rejected={
                request.draft.draft_id: request.violations for request in requests if request.violations
            },
        )
        # 10's own template copies are left out so the recommend template renders and counts the fallback
        return {
            card.draft_id: card.card_copy.model_copy(update={"critique": None})
            for card in written.cards
            if card.card_copy.copy_source != "template"
        }
