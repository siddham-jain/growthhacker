import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from uuid import UUID

from app.core.config import CardWriterSettings
from app.core.logging import get_logger
from app.domain.card_copy import CardCritique, Violation
from app.domain.recommendations import RecommendationDraft
from app.ports.llm import LLMClient
from app.services.llm.writer.critic import CardCritic, critic_flags
from app.services.llm.writer.evidence_pack import BuiltPack, EvidencePackBuilder
from app.services.llm.writer.schemas import WriterClientContext, WrittenCard, WrittenCards
from app.services.llm.writer.writer import CardWriter, WriteRequest, WriterOutcome

logger = get_logger(__name__)
MIN_REVIEWS_FOR_ADVISORY = 5

# called once per finished card, in completion order, for progress events
type CardCallback = Callable[[WrittenCard], Awaitable[None]]


class CardWritingService:
    """writes every draft's copy before the deadline: llm first, critic batches, one revision, template otherwise."""

    def __init__(
        self,
        writer: CardWriter,
        critic: CardCritic | None,
        settings: CardWriterSettings,
        *,
        builder: EvidencePackBuilder | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._writer = writer
        self._critic = critic if settings.critic_enabled else None
        self._settings = settings
        self._builder = builder or EvidencePackBuilder()
        self._monotonic = monotonic

    async def write(
        self,
        drafts: Sequence[RecommendationDraft],
        client: WriterClientContext,
        *,
        run_id: UUID | None,
        client_id: UUID | None,
        deadline_s: float | None = None,
        on_card: CardCallback | None = None,
        rejected: Mapping[str, Sequence[Violation]] | None = None,
    ) -> WrittenCards:
        deadline = self._monotonic() + (deadline_s if deadline_s is not None else self._settings.deadline_s)
        request = WriteRequest(run_id=run_id, client_id=client_id, deadline=deadline)
        packs = [self._builder.build(draft, client) for draft in drafts]
        rejections = [tuple((rejected or {}).get(draft.draft_id, ())) for draft in drafts]
        outcomes = await self._first_pass(packs, rejections, request)
        critiques = await self._critique(packs, outcomes, request)
        outcomes = await self._revise(packs, outcomes, critiques, request)
        cards = [
            WrittenCard(
                draft_id=draft.draft_id,
                stable_key=draft.stable_key,
                card_copy=outcome.card_copy,
                critic_flags=critic_flags(outcome.card_copy.critique),
            )
            for draft, outcome in zip(drafts, outcomes, strict=True)
        ]
        if on_card is not None:
            for card in cards:
                await on_card(card)
        sources = [card.card_copy.copy_source for card in cards]
        return WrittenCards(
            cards=cards,
            llm_cards=sources.count("llm"),
            revised_cards=sources.count("llm_revised"),
            template_cards=sources.count("template"),
            skipped_suspicious_videos=sum(built.suspicious_videos for built in packs),
        )

    def _remaining(self, request: WriteRequest) -> float:
        # the service always sets a deadline on its own requests
        return (request.deadline or 0.0) - self._monotonic()

    async def _bounded[T](
        self, jobs: list[Callable[[], Awaitable[T]]], request: WriteRequest
    ) -> list[T | None]:
        # cards still in flight at the deadline are cancelled; callers fill them deterministically
        semaphore = asyncio.Semaphore(self._settings.concurrency)

        async def run(job: Callable[[], Awaitable[T]]) -> T:
            async with semaphore:
                return await job()

        tasks = [asyncio.ensure_future(run(job)) for job in jobs]
        if not tasks:
            return []
        done, pending = await asyncio.wait(tasks, timeout=max(0.0, self._remaining(request)))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return [task.result() if task in done else None for task in tasks]

    async def _first_pass(
        self, packs: list[BuiltPack], rejections: list[tuple[Violation, ...]], request: WriteRequest
    ) -> list[WriterOutcome]:
        jobs = [
            lambda built=built, rejected=rejected: self._writer.write(built, request, rejected)
            for built, rejected in zip(packs, rejections, strict=True)
        ]
        results = await self._bounded(jobs, request)
        outcomes = []
        for built, result in zip(packs, results, strict=True):
            if result is None:
                result = WriterOutcome(self._writer.template.write(built, degrade_reason="deadline"), None, 0)
            outcomes.append(result)
        return outcomes

    async def _critique(
        self, packs: list[BuiltPack], outcomes: list[WriterOutcome], request: WriteRequest
    ) -> list[CardCritique | None]:
        critiques: list[CardCritique | None] = [None] * len(packs)
        if self._critic is None or self._remaining(request) < self._settings.min_attempt_s:
            return critiques
        indexes = [index for index, outcome in enumerate(outcomes) if outcome.llm_copy is not None]
        if not indexes:
            return critiques
        try:
            reviewed = await asyncio.wait_for(
                self._critic.review([(packs[i], outcomes[i]) for i in indexes], request),
                timeout=max(0.0, self._remaining(request)),
            )
        except TimeoutError:
            logger.warning("card_critic_deadline", cards=len(indexes))
            return critiques
        for index, critique in zip(indexes, reviewed, strict=True):
            critiques[index] = critique
        return critiques

    async def _revise(
        self,
        packs: list[BuiltPack],
        outcomes: list[WriterOutcome],
        critiques: list[CardCritique | None],
        request: WriteRequest,
    ) -> list[WriterOutcome]:
        reviewed = [critique for critique in critiques if critique is not None]
        revise_rate = sum(1 for c in reviewed if c.verdict == "revise") / len(reviewed) if reviewed else 0.0
        # an over-rejecting critic turns advisory for the run instead of rewriting everything
        advisory = (
            len(reviewed) >= MIN_REVIEWS_FOR_ADVISORY
            and revise_rate > self._settings.critic_advisory_revise_rate
        )
        if advisory:
            logger.warning("card_critic_advisory", revise_rate=round(revise_rate, 2), cards=len(reviewed))
        final = list(outcomes)
        to_revise: list[tuple[int, CardCritique]] = []
        for index, critique in enumerate(critiques):
            if critique is None:
                continue
            if critique.verdict == "revise" and not advisory:
                to_revise.append((index, critique))
            else:
                outcome = final[index]
                final[index] = WriterOutcome(
                    outcome.card_copy.model_copy(update={"critique": critique}),
                    outcome.llm_copy,
                    outcome.llm_calls,
                )
        jobs = [
            lambda i=i, critique=critique: self._writer.revise(packs[i], outcomes[i], critique, request)
            for i, critique in to_revise
        ]
        results = await self._bounded(jobs, request)
        for (index, critique), result in zip(to_revise, results, strict=True):
            original = outcomes[index]
            final[index] = result or WriterOutcome(
                original.card_copy.model_copy(update={"critique": critique}),
                original.llm_copy,
                original.llm_calls,
            )
        return final


def build_card_writing_service(llm: LLMClient, settings: CardWriterSettings) -> CardWritingService:
    return CardWritingService(CardWriter(llm, settings), CardCritic(llm, settings), settings)
