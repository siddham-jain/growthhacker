import json
import re

from pydantic import BaseModel, ConfigDict, Field

from app.ports.llm import LLMClient, PromptInput, TaskId, TrustedBlock
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import registrable_domain
from app.services.competitors.model import LINK_SIGNALS
from app.services.competitors.providers import structured
from app.services.competitors.select.assessment import AssessedCandidate

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
MAX_CHARS = 300
MAX_SUMMARIES = 30


class SummaryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    text: str = Field(max_length=600)


class SummaryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summaries: list[SummaryItem] = Field(max_length=MAX_SUMMARIES)


def source_phrases(item: AssessedCandidate) -> list[str]:
    phrases = []
    web_domains = sorted(
        {
            domain
            for source in item.entity.sources
            if source.kind == "web_search" and (domain := registrable_domain(source.ref))
        }
    )
    labels = {
        "user": "added by you",
        "website_hint": "mentioned on your website",
        "niche_search": "appears in YouTube searches for your topics",
        "llm": "suggested from your business profile",
        "rediscovery_news": "in recent news",
    }
    if web_domains:
        phrases.append(f"named on {', '.join(web_domains[:3])}")
    phrases.extend(label for kind, label in labels.items() if kind in item.entity.source_kinds)
    return phrases


def summary_facts(item: AssessedCandidate) -> dict[str, object]:
    facts: dict[str, object] = {"name": item.entity.brand_name, "sources": source_phrases(item)}
    facts["business_match"] = item.business.tier
    facts["geography"] = item.business.geo_basis
    facts["matched_offerings"] = len(item.business.matched_offerings)
    if item.primary is not None and item.primary.verdict is not None:
        facts["ownership"] = item.primary.label.value
        facts["ownership_signals"] = [
            signal.detail for signal in item.primary.verdict.signals if signal.weight > 0
        ][:3]
    assessment = item.primary_assessment
    if assessment is not None:
        facts["uploads_90d"] = assessment.evidence.uploads_90d
        facts["uploads_matching_topics"] = assessment.matching_uploads
        facts["uploads_checked"] = len(item.primary.uploads or [])
    return facts


def template_summary(item: AssessedCandidate) -> str:
    sources = source_phrases(item)
    first = f"{item.entity.brand_name}: {'; '.join(sources) if sources else 'found during discovery'}."
    if item.primary is None or item.primary.verdict is None:
        return f"{first} No YouTube channel confirmed yet."
    link = next(
        (signal.detail for signal in item.primary.verdict.signals if signal.name in LINK_SIGNALS), None
    )
    ownership = link or f"channel ownership is {item.primary.label.value}"
    assessment = item.primary_assessment
    if assessment is None:
        return f"{first} {ownership[:1].upper()}{ownership[1:]}."
    return (
        f"{first} {ownership[:1].upper()}{ownership[1:]}; {assessment.evidence.uploads_90d} uploads in the last "
        f"90 days, {assessment.matching_uploads} matching your topics."
    )


def grounded(text: str, facts: dict[str, object]) -> bool:
    allowed = set(NUMBER.findall(json.dumps(facts)))
    sentences = len(SENTENCE_END.findall(text.strip())) or 1
    return len(text) <= MAX_CHARS and sentences <= 2 and set(NUMBER.findall(text)) <= allowed


class EvidenceSummaryWriter:
    """phrases each candidate's evidence in two sentences; any unsupported number falls back to the template."""

    def __init__(self, llm: LLMClient | None) -> None:
        self.llm = llm

    async def write(self, items: list[AssessedCandidate], ctx: DiscoveryContext) -> dict[str, str]:
        texts = {item.entity.key: template_summary(item) for item in items}
        selected = [item for item in items if item.primary is not None][:MAX_SUMMARIES]
        if not selected:
            return texts
        facts = {item.entity.key: summary_facts(item) for item in selected}
        prompt = PromptInput(
            trusted=[
                TrustedBlock(
                    label="facts",
                    text=json.dumps(
                        [{"key": key, **value} for key, value in facts.items()], ensure_ascii=False
                    ),
                )
            ]
        )
        output = await structured(
            ctx,
            self.llm,
            task=TaskId.competitor_evidence_summary,
            schema=SummaryBatch,
            prompt_input=prompt,
            tier="low",
        )
        for summary in output.summaries if output else []:
            if summary.key in facts and grounded(summary.text, facts[summary.key]):
                texts[summary.key] = summary.text.strip()
        return texts
