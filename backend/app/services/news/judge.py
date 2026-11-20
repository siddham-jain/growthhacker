import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.news import ConfirmationStatus, EventType, NewsItem, RelevanceBand, SafetyFlag
from app.ports.llm import (
    CallBudget,
    CallContext,
    ClientContextBlock,
    LLMClient,
    LLMError,
    LLMErrorKind,
    PayloadClass,
    PromptInput,
    PromptRef,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry
from app.services.news.context import NewsClientContext
from app.services.news.tiers import TIER_RANK

PROMPT_ID = "news_judgement"
PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_STORY_MEMBERS = 5
MAX_OUTPUT_TOKENS = 1_500

EntityKind = Literal[
    "model", "product", "org", "drug", "device", "condition", "policy", "regulator", "person", "other"
]


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityLLM(_Schema):
    text: str = Field(max_length=120)
    kind: EntityKind
    item_ref: str
    quote: str = Field(max_length=200)


class OfferingLinkLLM(_Schema):
    offering_ref: str
    value_prop_refs: list[str] = []
    rationale: str = Field(max_length=240)


class NewsJudgementLLM(_Schema):
    relevance_band: RelevanceBand
    key_entity: EntityLLM
    other_entities: list[EntityLLM] = Field(default=[], max_length=8)
    event_type: EventType
    event_date: date | None
    confirmation: ConfirmationStatus
    offering_links: list[OfferingLinkLLM] = []
    buyer_question_refs: list[str] = []
    angle: str = Field(max_length=200)
    region_relevance: Literal["primary_region", "global", "other_region_only", "unclear"]
    about_client: bool
    about_competitor_refs: list[str] = []
    safety_flags: list[SafetyFlag] = []
    conflicting_reports: bool = False
    conversion_action_ref: str | None = None


NEWS_JUDGEMENT_V1 = prompt_registry.register(
    PromptSpec(
        prompt_id=PROMPT_ID,
        task=TaskId.news_judgement,
        version=1,
        system_text=load_prompt_text("news_judgement_v1.md", PROMPT_DIR),
        payload_classes=frozenset({PayloadClass.news, PayloadClass.client_profile}),
        output_schema=NewsJudgementLLM,
        changelog="initial news relevance judge",
    )
)


@dataclass(frozen=True)
class AliasMap:
    """short prompt labels -> real ids, so the model never sees or echoes raw ids."""

    items: dict[str, NewsItem] = field(default_factory=dict)
    offerings: dict[str, str] = field(default_factory=dict)
    value_props: dict[str, str] = field(default_factory=dict)
    buyer_questions: dict[str, UUID] = field(default_factory=dict)
    conversion_actions: dict[str, str] = field(default_factory=dict)
    competitors: dict[str, UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeOutcome:
    story_id: UUID
    output: NewsJudgementLLM | None
    aliases: AliasMap
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    suppression: str | None = None


def story_members(items: Sequence[NewsItem]) -> list[NewsItem]:
    ranked = sorted(items, key=lambda i: (-TIER_RANK[i.source_tier], "paywalled" in i.flags, i.url_hash))
    return ranked[:MAX_STORY_MEMBERS]


def build_aliases(ctx: NewsClientContext, members: Sequence[NewsItem]) -> AliasMap:
    profile = ctx.profile
    return AliasMap(
        items={f"n{i}": item for i, item in enumerate(members, 1)},
        offerings={f"o{i}": o.id for i, o in enumerate(profile.offerings, 1)},
        value_props={f"v{i}": v.id for i, v in enumerate(profile.value_propositions, 1)},
        buyer_questions={f"q{i}": q.id for i, q in enumerate(ctx.active_buyer_questions, 1)},
        conversion_actions={f"a{i}": a.id for i, a in enumerate(profile.conversion_actions, 1)},
        competitors={f"c{i}": c.id for i, c in enumerate(ctx.confirmed_competitors, 1)},
    )


def client_context_text(ctx: NewsClientContext, aliases: AliasMap) -> str:
    profile = ctx.profile
    offerings = {o.id: o for o in profile.offerings}
    value_props = {v.id: v for v in profile.value_propositions}
    questions = {q.id: q for q in ctx.active_buyer_questions}
    actions = {a.id: a for a in profile.conversion_actions}
    competitors = {c.id: c for c in ctx.confirmed_competitors}
    lines = [
        f"business: {profile.identity.name} ({profile.identity.niche})",
        f"summary: {profile.identity.one_liner}",
        f"regions: {', '.join(ctx.regions) or 'unknown'}; languages: {', '.join(ctx.languages)}",
        f"regulated health business: {'yes' if ctx.is_health else 'no'}",
        "offerings:",
        *(f"  {ref}: {offerings[oid].name}" for ref, oid in aliases.offerings.items()),
        "value propositions:",
        *(f"  {ref}: {value_props[vid].statement}" for ref, vid in aliases.value_props.items()),
        "buyer questions:",
        *(f"  {ref}: {questions[qid].text}" for ref, qid in aliases.buyer_questions.items()),
        "conversion actions:",
        *(f"  {ref}: {actions[aid].label}" for ref, aid in aliases.conversion_actions.items()),
        "competitors:",
        *(f"  {ref}: {competitors[cid].brand_name}" for ref, cid in aliases.competitors.items()),
        f"excluded topics: {', '.join(ctx.freshness.excluded_topics) or 'none'}",
    ]
    return "\n".join(lines)


def item_block(ref: str, item: NewsItem) -> UntrustedBlock:
    published = item.published_at.isoformat() if item.published_at else "unknown"
    text = (
        f"outlet: {item.domain}\ntier: {item.source_tier.value}\npublished: {published}\n"
        f"title: {item.title}\nsnippet: {item.snippet}"
    )
    return UntrustedBlock(label=ref, source=item.domain, text=text)


class NewsRelevanceJudge:
    """bounded judgement edge: categorical labels, id links and verbatim quotes only."""

    def __init__(self, llm: LLMClient, concurrency: int) -> None:
        self._llm = llm
        self._semaphore = asyncio.Semaphore(concurrency)

    async def judge(
        self,
        ctx: NewsClientContext,
        story_id: UUID,
        items: Sequence[NewsItem],
        *,
        now: datetime,
        run_id: UUID | None,
    ) -> JudgeOutcome:
        members = story_members(items)
        aliases = build_aliases(ctx, members)
        prompt = PromptInput(
            client_context=ClientContextBlock(text=client_context_text(ctx, aliases)),
            trusted=[TrustedBlock(label="today", text=f"today (UTC): {now.date().isoformat()}")],
            untrusted=[item_block(ref, item) for ref, item in aliases.items.items()],
        )
        context = CallContext(
            run_id=run_id,
            client_id=ctx.client_id,
            stage="news",
            payload_classes=frozenset({PayloadClass.news, PayloadClass.client_profile}),
            idempotency_key=f"news_judgement:{ctx.client_id}:{story_id}:{ctx.profile_version}",
            language=ctx.languages[0],
        )
        async with self._semaphore:
            try:
                result = await self._llm.generate_structured(
                    task=TaskId.news_judgement,
                    schema=NewsJudgementLLM,
                    system=PromptRef(prompt_id=PROMPT_ID),
                    input=prompt,
                    context=context,
                    tier="medium",
                    budget=CallBudget(max_output_tokens=MAX_OUTPUT_TOKENS, max_attempts=2),
                )
            except LLMError as error:
                return JudgeOutcome(story_id, None, aliases, suppression=_suppression_for(error))
        return JudgeOutcome(
            story_id,
            result.value,
            aliases,
            model=result.model,
            tokens_in=result.usage.input_tokens + result.usage.cache_read_tokens,
            tokens_out=result.usage.output_tokens,
        )


def _suppression_for(error: LLMError) -> str:
    if error.kind is LLMErrorKind.refusal:
        return "llm_refusal"
    if error.kind is LLMErrorKind.budget_exceeded:
        return "llm_budget"
    return "llm_unavailable"
