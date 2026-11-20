from dataclasses import dataclass, field

from app.services.llm.writer.schemas import WrittenCard, WrittenCards
from app.services.recommend.inputs import ClientFacts
from app.services.recommend.llm_writer import CardWritingServiceWriter
from app.services.recommend.writer import CardRequest, TemplateCardRenderer, WriterFacts
from tests.recommend.fixtures import CLIENT_ID, RUN_ID


@dataclass
class FakeService:
    """stands in for 10's CardWritingService; returns template-rendered copy tagged with a source."""

    source: str
    client: ClientFacts
    seen: list = field(default_factory=list)

    async def write(self, drafts, context, *, run_id, client_id, deadline_s=None, on_card=None):
        self.seen = [context, run_id, client_id]
        renderer = TemplateCardRenderer()
        cards = [
            WrittenCard(
                draft_id=draft.draft_id,
                stable_key=draft.stable_key,
                card_copy=renderer.render(draft, self.client).model_copy(update={"copy_source": self.source}),
            )
            for draft in drafts
        ]
        return WrittenCards(
            cards=cards, llm_cards=0, revised_cards=0, template_cards=0, skipped_suspicious_videos=0
        )


async def test_adapter_passes_client_context_and_returns_llm_copy(service, inputs) -> None:
    plan = service.plan(inputs, 30)
    facts = WriterFacts(topic_labels={"tr_x": "x"}, recent_titles=("Own title",))
    requests = [CardRequest(draft, plan.ctx.client, facts, RUN_ID) for draft in plan.selection.selected[:2]]
    fake = FakeService("llm_revised", plan.ctx.client)

    written = await CardWritingServiceWriter(fake).write(requests)

    assert set(written) == {request.draft.draft_id for request in requests}
    context, run_id, client_id = fake.seen
    assert (context.name, context.recent_titles, run_id, client_id) == (
        "Apollo Hospitals",
        ["Own title"],
        RUN_ID,
        CLIENT_ID,
    )


async def test_adapter_leaves_template_copies_to_the_recommend_fallback(service, inputs) -> None:
    plan = service.plan(inputs, 30)
    requests = [CardRequest(draft, plan.ctx.client) for draft in plan.selection.selected[:2]]

    assert await CardWritingServiceWriter(FakeService("template", plan.ctx.client)).write(requests) == {}
    assert await CardWritingServiceWriter(FakeService("llm", plan.ctx.client)).write([]) == {}
