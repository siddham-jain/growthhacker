import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, create_model

from app.core.config import TopicEngineSettings
from app.domain.topics import TopicAlias, TopicRef
from app.ports.llm import LLMClient, LLMError, PromptInput, TaskId, TrustedBlock
from app.services.topics.llm import SpendGuard, call_structured, enum_of
from app.services.topics.universe.aliases import make_alias
from app.services.topics.versions import ALIAS_PROMPT_VERSION

ProposedKind = Literal["translation", "synonym", "transliteration", "spelling_variant", "abbreviation"]


class ProposedAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    lang: str
    script: Literal["Latn", "Deva", "Gujr", "Arab", "Taml", "Telu", "Beng", "Knda", "Mlym", "Guru", "Orya"]
    kind: ProposedKind


@dataclass(frozen=True)
class Proposal:
    aliases: list[TopicAlias] = field(default_factory=list)
    negative_contexts: list[str] = field(default_factory=list)


def proposal_schema(ref_ids: Sequence[str]) -> type[BaseModel]:
    ref_proposal = create_model(
        "RefAliasProposal",
        __config__=ConfigDict(extra="forbid"),
        topic_id=(enum_of(ref_ids), ...),
        aliases=(list[ProposedAlias], ...),
        negative_contexts=(list[str], ...),
    )
    return create_model(
        "AliasProposals", __config__=ConfigDict(extra="forbid"), refs=(list[ref_proposal], ...)
    )


class LLMAliasProposer:
    """translations, synonyms and hinglish spellings for client terms; results stay proposed."""

    def __init__(self, llm: LLMClient, settings: TopicEngineSettings, guard: SpendGuard) -> None:
        self._llm = llm
        self._settings = settings
        self._guard = guard
        self.calls = 0
        self.warnings: list[str] = []

    async def propose(
        self, refs: Sequence[TopicRef], languages: Sequence[str], *, client_id: UUID, run_id: UUID | None
    ) -> dict[str, Proposal]:
        proposals: dict[str, Proposal] = {}
        batch_size = self._settings.proposer_batch_refs
        for start in range(0, len(refs), batch_size):
            batch = list(refs[start : start + batch_size])
            try:
                proposals.update(await self._propose_batch(batch, languages, client_id, run_id))
            except LLMError as error:
                # one failed batch only costs that batch its proposals
                self.warnings.append(f"alias_proposals_unavailable:{error.kind.value}")
        return proposals

    async def _propose_batch(
        self, refs: list[TopicRef], languages: Sequence[str], client_id: UUID, run_id: UUID | None
    ) -> dict[str, Proposal]:
        payload = {
            "languages": list(languages),
            "topics": [
                {"topic_id": ref.id, "kind": ref.kind.value, "label": ref.label, "definition": ref.definition}
                for ref in refs
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        schema = proposal_schema([ref.id for ref in refs])
        result = await call_structured(
            self._llm,
            task=TaskId.topic_alias_proposal,
            schema=schema,
            prompt_id="topic_alias_proposal",
            prompt_input=PromptInput(trusted=[TrustedBlock(label="client_topics", text=text)]),
            client_id=client_id,
            run_id=run_id,
            stage="topics",
            guard=self._guard,
            estimated_usd=self._settings.adj_estimated_usd_per_batch,
            idempotency_key=hashlib.sha256(f"{ALIAS_PROMPT_VERSION}|{text}".encode()).hexdigest(),
        )
        self.calls += 1
        by_id = {ref.id: ref for ref in refs}
        proposals: dict[str, Proposal] = {}
        for item in result.value.refs:
            ref = by_id.get(item.topic_id)
            if ref is None:
                continue
            aliases = []
            for proposed in item.aliases[: self._settings.proposer_max_aliases]:
                alias = make_alias(
                    proposed.text,
                    kind=proposed.kind,
                    origin="llm_proposed",
                    status="proposed",
                    anchor=ref.label,
                    weight=self._settings.proposed_alias_weight,
                    lang=proposed.lang,
                )
                if alias is not None:
                    aliases.append(alias)
            negatives = [term.strip() for term in item.negative_contexts if term.strip()]
            proposals[ref.id] = Proposal(aliases=aliases, negative_contexts=negatives[:10])
        return proposals
