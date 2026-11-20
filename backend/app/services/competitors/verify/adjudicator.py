from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import CompetitorSettings
from app.domain.youtube import OwnershipLabel, OwnershipSignal, OwnershipVerdict
from app.ports.llm import LLMClient, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.providers import structured

BUNDLE_FIELDS = frozenset({"channel_title", "channel_description", "recent_upload_titles"})
OFFICIAL = frozenset({"official", "regional_official"})
NOT_OFFICIAL = frozenset({"unofficial", "fan", "reupload", "news_or_media", "unrelated"})


class Adjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal[
        "official", "regional_official", "unofficial", "fan", "reupload", "news_or_media", "unrelated"
    ]
    reason: str = Field(max_length=400)
    cited_signal_names: list[str] = Field(min_length=1)


class LlmOwnershipAdjudicator:
    """reviews uncertain channels without link evidence; it can reach likely or rejected, never verified."""

    def __init__(self, llm: LLMClient | None, settings: CompetitorSettings, max_calls: int = 8) -> None:
        self.llm = llm
        self.weights = settings.ownership
        self.remaining = max_calls

    def needs_review(self, lead: ChannelLead) -> bool:
        return lead.record is not None and lead.label is OwnershipLabel.uncertain and not lead.has_link_signal

    async def adjudicate(self, entity: CandidateEntity, lead: ChannelLead, ctx: DiscoveryContext) -> None:
        if self.llm is None or self.remaining <= 0 or not self.needs_review(lead):
            return
        self.remaining -= 1
        record, verdict = lead.record, lead.verdict
        signals = "\n".join(
            f"- {signal.name} ({signal.weight:+.2f}): {signal.detail}" for signal in verdict.signals
        )
        titles = "\n".join(f"- {video.title}" for video in (lead.uploads or [])[:10])
        prompt = PromptInput(
            trusted=[
                TrustedBlock(
                    label="brand",
                    text=f"Brand: {entity.brand_name}\nDomain: {entity.official_domain or 'unknown'}\nSignals:\n{signals or '- none'}",
                )
            ],
            untrusted=[
                UntrustedBlock(label="channel_title", source="youtube", text=record.title),
                UntrustedBlock(label="channel_description", source="youtube", text=record.description[:1000]),
                UntrustedBlock(label="recent_upload_titles", source="youtube", text=titles or "none"),
            ],
        )
        output = await structured(
            ctx,
            self.llm,
            task=TaskId.competitor_ownership_adjudication,
            schema=Adjudication,
            prompt_input=prompt,
            tier="low",
        )
        if output is None:
            return
        allowed = {signal.name for signal in verdict.signals} | BUNDLE_FIELDS
        if not set(output.cited_signal_names) <= allowed:
            return
        lead.verdict = self._apply(verdict, output)

    def _apply(self, verdict: OwnershipVerdict, output: Adjudication) -> OwnershipVerdict:
        note = OwnershipSignal(name="llm_adjudication", weight=0.0, detail=f"reviewed as {output.verdict}")
        signals = [*verdict.signals, note]
        reason = output.reason[:300]
        if (
            output.verdict in OFFICIAL
            and self.weights.adjudication_min <= verdict.score < self.weights.verified_min
        ):
            return verdict.model_copy(
                update={"label": OwnershipLabel.likely, "signals": signals, "llm_reason": reason}
            )
        if output.verdict in NOT_OFFICIAL:
            return verdict.model_copy(
                update={"label": OwnershipLabel.rejected, "signals": signals, "llm_reason": reason}
            )
        return verdict.model_copy(update={"signals": signals, "llm_reason": reason})
