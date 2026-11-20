import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar, Protocol

from app.core.config import Settings
from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.common import DomainModel
from app.domain.recommendations import RecommendationDraft, recommendation_id
from app.domain.runs import CardWrittenPayload, RunNotice, RunNoticeCode
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.ports.artifacts import ArtifactKey
from app.services.llm.budgets import LLMBudgetLedger, run_cap_usd
from app.services.llm.writer.copy_store import RecommendationCopyStore
from app.services.llm.writer.schemas import WriterClientContext, WrittenCard, WrittenCards
from app.services.llm.writer.service import CardWritingService
from app.services.llm.writer.specs import CARD_CRITIQUE_V1, CARD_WRITE_V1
from app.services.llm.writer.template import TEMPLATE_VERSION

WRITTEN_CARDS: ArtifactKey[WrittenCards] = ArtifactKey(
    name="write_cards", model=WrittenCards, schema_version=1, contains_api_data=True
)
# template copy caused by the provider side, not by the evidence, degrades the stage
SERVICE_DEGRADE_REASONS = frozenset({"llm_unavailable", "budget", "deadline"})
FALLBACK_MESSAGE = "Some cards use standard wording because our writing service is temporarily unavailable."
BUDGET_MESSAGE = "Some cards use standard wording today because the writing budget was reached."


class CardInputs(DomainModel):
    drafts: list[RecommendationDraft]
    client: WriterClientContext


class CardInputSource(Protocol):
    # gated drafts for the run's window (07) plus the client facts the pack builder needs (02/03/04)
    async def load(self, ctx: RunContext[Any]) -> CardInputs: ...


class WriteCardsDeps(Protocol):
    settings: Settings
    card_inputs: CardInputSource
    card_writing: CardWritingService
    llm_budget: LLMBudgetLedger
    recommendation_copy: RecommendationCopyStore


def fallback_notice(cards: list[WrittenCard]) -> RunNotice | None:
    reasons = [card.card_copy.degrade_reason for card in cards if card.card_copy.copy_source == "template"]
    if not reasons:
        return None
    message = BUDGET_MESSAGE if "budget" in reasons else FALLBACK_MESSAGE
    return RunNotice(
        code=RunNoticeCode.llm_fallback,
        stage="write_cards",
        message=message,
        detail={"template_cards": len(reasons), "reasons": sorted({reason or "none" for reason in reasons})},
    )


@stage_registry.register("write_cards")
class WriteCardsStage:
    """evidence-grounded card copy for the run's drafts; template wording whenever the llm cannot deliver."""

    name: ClassVar[str] = "write_cards"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("recommend",)
    optional_deps: ClassVar[tuple[str, ...]] = ()
    produces: ClassVar[ArtifactKey[WrittenCards]] = WRITTEN_CARDS
    policy: ClassVar[StagePolicy] = StagePolicy(timeout_s=90, soft_timeout_s=60, max_attempts=1)
    weight: ClassVar[float] = 15.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[WriteCardsDeps]) -> Mapping[str, Any]:
        return {
            "card_write": CARD_WRITE_V1.prompt_version,
            "card_critique": CARD_CRITIQUE_V1.prompt_version,
            "template": TEMPLATE_VERSION,
            "llm_enabled": ctx.deps.settings.llm.enabled,
        }

    def _deadline_s(self, ctx: RunContext[WriteCardsDeps]) -> float:
        configured = ctx.deps.settings.llm.writer.deadline_s
        if ctx.soft_deadline is None:
            return configured
        # the runner sets soft deadlines on the event loop clock
        return max(0.0, min(configured, ctx.soft_deadline - asyncio.get_running_loop().time()))

    async def run(self, ctx: RunContext[WriteCardsDeps]) -> StageResult[WrittenCards]:
        deps = ctx.deps
        await deps.llm_budget.open_run(ctx.run_id, run_cap_usd(ctx.kind, deps.settings.llm.budgets))
        inputs = await deps.card_inputs.load(ctx)
        drafts = {draft.draft_id: draft for draft in inputs.drafts}
        total = len(drafts)
        await ctx.reporter.step("write.start", f"Writing {total} recommendation cards")
        done = 0

        async def on_card(card: WrittenCard) -> None:
            nonlocal done
            done += 1
            draft = drafts[card.draft_id]
            await ctx.reporter.step(
                "write.card",
                card.card_copy.summary[:200],
                CardWrittenPayload(
                    recommendation_id=recommendation_id(card.stable_key),
                    type=draft.type.value,
                    summary=card.card_copy.summary,
                    lens=sorted({assignment.lens.value for assignment in draft.lenses}),
                    confidence_label=draft.confidence.label.value if draft.confidence else "Low",
                ),
            )
            await ctx.reporter.progress(done, total, "cards")

        written = await deps.card_writing.write(
            inputs.drafts,
            inputs.client,
            run_id=ctx.run_id,
            client_id=ctx.client_id,
            deadline_s=self._deadline_s(ctx),
            on_card=on_card,
        )
        await deps.recommendation_copy.save(
            TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id), ctx.run_id, written.cards
        )
        notice = fallback_notice(written.cards)
        service_degraded = any(
            card.card_copy.degrade_reason in SERVICE_DEGRADE_REASONS for card in written.cards
        )
        return StageResult(
            status="degraded" if service_degraded else "succeeded",
            artifact=written,
            counts={
                "cards": len(written.cards),
                "llm_cards": written.llm_cards,
                "revised_cards": written.revised_cards,
                "template_cards": written.template_cards,
            },
            notices=(notice,) if notice is not None else (),
        )
