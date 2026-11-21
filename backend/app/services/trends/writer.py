import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.domain.topics import TopicRef
from app.domain.trends import KeywordTrend
from app.ports.llm import (
    CallBudget,
    CallContext,
    ClientContextBlock,
    LLMClient,
    LLMError,
    PayloadClass,
    PromptInput,
    PromptRef,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.ports.search import WebSearchHit
from app.services.llm.prompts import PromptSpec, load_prompt_text, prompt_registry
from app.services.trends.candidates import WebKeyword
from app.services.trends.ideas import DraftIdea, DraftIdeas, DraftWebKeywords

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
KEYWORDS_PROMPT = "trend_keywords"
IDEAS_PROMPT = "trend_ideas"
KEYWORD_CLASSES = frozenset({PayloadClass.public_web, PayloadClass.client_profile})
IDEA_CLASSES = frozenset({PayloadClass.public_youtube, PayloadClass.client_profile})

TREND_KEYWORDS_V2 = prompt_registry.register(
    PromptSpec(
        prompt_id=KEYWORDS_PROMPT,
        task=TaskId.trend_keywords,
        version=2,
        system_text=load_prompt_text("trend_keywords_v2.md", PROMPT_DIR),
        payload_classes=KEYWORD_CLASSES,
        output_schema=DraftWebKeywords,
        changelog="shorter subject keywords that video titles actually contain",
    )
)
TREND_IDEAS_V1 = prompt_registry.register(
    PromptSpec(
        prompt_id=IDEAS_PROMPT,
        task=TaskId.trend_ideas,
        version=1,
        system_text=load_prompt_text("trend_ideas_v1.md", PROMPT_DIR),
        payload_classes=IDEA_CLASSES,
        output_schema=DraftIdeas,
        changelog="initial trend video ideas from measured keyword evidence",
    )
)


@dataclass(frozen=True)
class ClientBrief:
    client_id: UUID
    run_id: UUID | None
    text: str
    language: str
    buyer_questions: Sequence[str] = ()
    own_titles: Sequence[str] = ()


class TrendWriter:
    """the two llm steps: keywords from web results and ideas from measured videos; both may fail to nothing."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.calls = 0

    async def web_keywords(
        self,
        brief: ClientBrief,
        topics: Sequence[TopicRef],
        hits: Sequence[WebSearchHit],
        limit: int,
        now: datetime,
    ) -> list[WebKeyword]:
        if not hits or not topics or limit <= 0:
            return []
        prompt = PromptInput(
            client_context=ClientContextBlock(text=brief.text),
            trusted=[
                TrustedBlock(label="today", text=f"today (UTC): {now.date().isoformat()}"),
                TrustedBlock(label="topics", text="\n".join(f"{ref.id}: {ref.label}" for ref in topics)),
                TrustedBlock(label="limit", text=f"return at most {limit} keywords"),
            ],
            untrusted=[
                UntrustedBlock(
                    label=f"web_result_{index}",
                    source="tavily",
                    text=f"url: {hit.url}\ntitle: {hit.title}\nsnippet: {hit.snippet}",
                )
                for index, hit in enumerate(hits, 1)
            ],
        )
        self.calls += 1
        try:
            result = await self._llm.generate_structured(
                task=TaskId.trend_keywords,
                schema=DraftWebKeywords,
                system=PromptRef(prompt_id=KEYWORDS_PROMPT),
                input=prompt,
                context=self._context(
                    brief, "trends", KEYWORD_CLASSES, f"trend_keywords:{brief.client_id}:{now.date()}"
                ),
                tier="low",
                budget=CallBudget(max_output_tokens=2048, max_attempts=2),
            )
        except LLMError as error:
            logger.warning("trend keyword proposal failed", extra={"kind": error.kind.value})
            return []
        urls = {hit.url for hit in hits}
        keywords = []
        for item in result.value.keywords[:limit]:
            cited = [url for url in item.source_urls if url in urls]
            # a keyword with no real source is a guess, not a trend
            if cited:
                keywords.append(WebKeyword(item.keyword.lower(), item.topic_ref_id, item.reason, cited))
        return keywords

    async def ideas(
        self, brief: ClientBrief, trends: Sequence[KeywordTrend], per_keyword: int, now: datetime
    ) -> list[DraftIdea]:
        if not trends:
            return []
        blocks = [
            UntrustedBlock(label=f"keyword_{index}", source="youtube_public", text=_keyword_text(trend))
            for index, trend in enumerate(trends, 1)
        ]
        trusted = [
            TrustedBlock(label="ideas_per_keyword", text=f"write at most {per_keyword} ideas per keyword"),
            TrustedBlock(label="own_recent_titles", text="\n".join(brief.own_titles[:30]) or "none"),
        ]
        self.calls += 1
        try:
            result = await self._llm.generate_structured(
                task=TaskId.trend_ideas,
                schema=DraftIdeas,
                system=PromptRef(prompt_id=IDEAS_PROMPT),
                input=PromptInput(
                    client_context=ClientContextBlock(text=brief.text), trusted=trusted, untrusted=blocks
                ),
                context=self._context(brief, "trends", IDEA_CLASSES, None),
                tier="medium",
                budget=CallBudget(max_output_tokens=6000, max_attempts=2),
            )
        except LLMError as error:
            logger.warning("trend ideas failed", extra={"kind": error.kind.value})
            return []
        return list(result.value.ideas)

    @staticmethod
    def _context(
        brief: ClientBrief, stage: str, classes: frozenset[PayloadClass], key: str | None
    ) -> CallContext:
        return CallContext(
            run_id=brief.run_id,
            client_id=brief.client_id,
            stage=stage,
            payload_classes=classes,
            idempotency_key=key,
            language=brief.language,
        )


def _keyword_text(trend: KeywordTrend) -> str:
    lines = [f"keyword: {trend.candidate.keyword}", f"client topic: {trend.candidate.topic_label}"]
    if trend.candidate.web_reason:
        lines.append(f"in the news: {trend.candidate.web_reason}")
    for video in trend.videos:
        owner = "competitor" if video.is_competitor else "other channel"
        lines.append(
            f"[{video.video_id}] {video.title} | {video.channel_title} ({owner}) | "
            f"{video.views_per_day:.0f} views a day | {'short' if (video.duration_s or 0) <= 60 else 'long'}"
        )
    return "\n".join(lines)
