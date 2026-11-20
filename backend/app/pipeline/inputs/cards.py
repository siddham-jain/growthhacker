from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.tenancy import TenantScope
from app.domain.recommendations import RecommendationDraft
from app.models.recommendations import Recommendation as RecommendationRow
from app.pipeline.contracts import RunContext
from app.pipeline.inputs.common import ProfileMissing, profile_and_questions
from app.pipeline.stages.cards import CardInputs
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.services.llm.writer.schemas import WriterClientContext
from app.services.recommend.inputs import ClientFacts


class TemplateCardInputs:
    """drafts of the run's window whose cards are still worded with the template.

    recommend already writes the selected window through CardWritingService (C102), so this stage only
    retries the cards that fell back; drafts the llm already worded are not written twice.
    """

    def __init__(self, container: Any) -> None:
        self._container = container

    async def load(self, ctx: RunContext[Any]) -> CardInputs:
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        profile, questions = await profile_and_questions(self._container, scope)
        if profile is None:
            raise ProfileMissing("the client has no business profile; finish onboarding first")
        card_run_id = await self._card_run_id(ctx)
        statement = (
            select(RecommendationRow.draft)
            .where(
                RecommendationRow.workspace_id == ctx.workspace_id,
                RecommendationRow.client_id == ctx.client_id,
                RecommendationRow.run_id == card_run_id,
                RecommendationRow.window_days == ctx.window_days,
                RecommendationRow.status == "shown",
                RecommendationRow.writer_meta["rendered_by"].astext == "template",
            )
            .order_by(RecommendationRow.rank.nulls_last(), RecommendationRow.rec_id)
        )
        async with self._container.session_factory() as session:
            payloads = (await session.execute(statement)).scalars().all()
        facts = ClientFacts.from_profile(profile)
        client = WriterClientContext(
            name=facts.name,
            vertical=facts.vertical or "business",
            language=facts.language,
            offerings=facts.offerings,
            value_props=facts.value_propositions,
            buyer_questions={question.id: question.text for question in questions},
            locations=facts.locations,
            domains=facts.website_domains,
            topic_labels={ref.id: ref.label for ref in ctx.snapshot.topic_refs},
        )
        drafts = [RecommendationDraft.model_validate(payload) for payload in payloads if payload]
        return CardInputs(drafts=drafts, client=client, card_run_id=card_run_id)

    @staticmethod
    async def _card_run_id(ctx: RunContext[Any]) -> UUID:
        # a cards run inherits recommend, whose cards still name the run that saved their rows
        output = await ctx.artifacts.get(RECOMMEND_ARTIFACT)
        window = output.window(ctx.window_days)
        cards = [*window.set.recommendations, *window.refresh_cards] if window else []
        return next((card.extras.run_id for card in cards), ctx.run_id)
