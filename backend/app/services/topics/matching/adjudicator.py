import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, create_model

from app.core.config import TopicEngineSettings
from app.core.db import UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.topics import PreparedVideoText
from app.ports.llm import (
    LLMBudgetExceeded,
    LLMClient,
    LLMError,
    LLMErrorKind,
    PromptInput,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.services.topics.llm import SpendGuard, call_structured, enum_of
from app.services.topics.matching.combine import DecisionPolicy, PairState
from app.services.topics.matching.quotes import adjudication_fields, verify_quote
from app.services.topics.prompt_specs import SPECS
from app.services.topics.repositories import AdjudicationRepository

EvidenceField = Literal["title", "chapters", "description", "tags", "hashtags"]


@dataclass
class AdjudicationItem:
    video: PreparedVideoText
    pairs: list[PairState]
    observed_at: datetime
    expires_at: datetime
    priority: tuple[int, float] = (9, 0.0)
    cache_key: str = ""


@dataclass
class AdjudicationStats:
    calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    degraded: set[str] = field(default_factory=set)


class _Outcome(BaseModel):
    matches: list[dict[str, str]]
    none: bool


def verdict_schema(video_keys: Sequence[str], topic_ids: Sequence[str]) -> type[BaseModel]:
    match = create_model(
        "TopicEvidence",
        __config__=ConfigDict(extra="forbid"),
        topic_id=(enum_of(topic_ids), ...),
        evidence_quote=(str, ...),
        evidence_field=(EvidenceField, ...),
    )
    decision = create_model(
        "VideoDecision",
        __config__=ConfigDict(extra="forbid"),
        video_key=(enum_of(video_keys), ...),
        matches=(list[match], ...),
        none=(bool, ...),
    )
    return create_model(
        "TopicAdjudication", __config__=ConfigDict(extra="forbid"), decisions=(list[decision], ...)
    )


# the prompt's content version, so an edited prompt never reuses verdicts given under the old one
ADJUDICATION_PROMPT_VERSION = SPECS[TaskId.topic_adjudication].prompt_version


def cache_key(model_key: str, item: AdjudicationItem) -> str:
    fingerprints = sorted(pair.ref.fingerprint for pair in item.pairs)
    raw = json.dumps([ADJUDICATION_PROMPT_VERSION, model_key, item.video.prepared_hash, fingerprints])
    return hashlib.sha256(raw.encode()).hexdigest()


class LLMAdjudicator:
    """ambiguous band only; the model can pick among the given client topic ids or none, with a quote."""

    def __init__(
        self,
        llm: LLMClient | None,
        settings: TopicEngineSettings,
        policy: DecisionPolicy,
        guard: SpendGuard,
        *,
        model_key: str,
        client_id: UUID,
        run_id: UUID | None,
        uow_factory: Callable[[], UnitOfWork] | None = None,
        scope: TenantScope | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._policy = policy
        self._guard = guard
        self._model_key = model_key
        self._client_id = client_id
        self._run_id = run_id
        self._uow_factory = uow_factory
        self._scope = scope
        self._llm_down = False
        self._budget_gone = False
        self.stats = AdjudicationStats()

    async def adjudicate(self, items: Sequence[AdjudicationItem], max_videos: int) -> AdjudicationStats:
        ordered = sorted(items, key=lambda item: item.priority)
        eligible, over_budget = ordered[:max_videos], ordered[max_videos:]
        for item in over_budget:
            self._degrade(item, "llm_budget")
        for item in eligible:
            item.pairs = sorted(item.pairs, key=lambda pair: -pair.combined)[
                : self._settings.adj_max_candidates
            ]
            item.cache_key = cache_key(self._model_key, item)
        pending = await self._apply_cached(eligible)
        if self._llm is None:
            for item in pending:
                self._degrade(item, "no_llm")
            return self.stats
        semaphore = asyncio.Semaphore(self._settings.adj_concurrency)
        size = self._settings.adj_batch_videos
        batches = [pending[start : start + size] for start in range(0, len(pending), size)]

        async def run(batch: list[AdjudicationItem]) -> None:
            async with semaphore:
                await self._run_batch(batch, retry_missing=True)

        await asyncio.gather(*(run(batch) for batch in batches))
        return self.stats

    def _degrade(self, item: AdjudicationItem, reason: str) -> None:
        self.stats.degraded.add(reason)
        for pair in item.pairs:
            if pair.decision == "pending":
                self._policy.without_llm(pair, reason)

    async def _apply_cached(self, items: list[AdjudicationItem]) -> list[AdjudicationItem]:
        if self._uow_factory is None or self._scope is None or not items:
            return items
        async with self._uow_factory() as uow:
            cached = await AdjudicationRepository(uow.session, self._scope).get_many(
                [item.cache_key for item in items]
            )
        pending = []
        for item in items:
            verdict = cached.get(item.cache_key)
            if verdict is None:
                pending.append(item)
                continue
            self.stats.cache_hits += 1
            self._apply(item, _Outcome(**verdict["outcome"]), verdict.get("detail", {}))
        return pending

    async def _run_batch(self, batch: list[AdjudicationItem], *, retry_missing: bool) -> None:
        if not batch:
            return
        if self._llm_down or self._budget_gone:
            for item in batch:
                self._degrade(item, "llm_budget" if self._budget_gone else "no_llm")
            return
        keys = {f"v{index + 1}": item for index, item in enumerate(batch)}
        try:
            result = await self._call(keys)
        except LLMBudgetExceeded:
            self._budget_gone = True
            for item in batch:
                self._degrade(item, "llm_budget")
            return
        except LLMError as error:
            if error.kind is LLMErrorKind.refusal and len(batch) > 1:
                middle = len(batch) // 2
                await self._run_batch(batch[:middle], retry_missing=retry_missing)
                await self._run_batch(batch[middle:], retry_missing=retry_missing)
                return
            if error.kind is not LLMErrorKind.refusal:
                self._llm_down = True
            for item in batch:
                self._degrade(item, "no_llm")
            return
        self.stats.calls += 1
        self.stats.tokens_in += result.usage.input_tokens
        self.stats.tokens_out += result.usage.output_tokens
        self.stats.cost_usd += result.cost_usd
        detail = {
            "model": result.model,
            "prompt_version": result.prompt_version,
            "request_id": result.request_ids[0] if result.request_ids else None,
        }
        answered: dict[str, _Outcome] = {}
        for decision in result.value.decisions:
            item = keys.get(decision.video_key)
            if item is None or decision.video_key in answered:
                continue
            matches = [
                {"topic_id": match.topic_id, "quote": match.evidence_quote, "field": match.evidence_field}
                for match in decision.matches
            ]
            if matches or decision.none:
                answered[decision.video_key] = _Outcome(matches=matches, none=decision.none and not matches)
        rows = []
        for key, item in keys.items():
            outcome = answered.get(key)
            if outcome is None:
                continue
            self._apply(item, outcome, detail)
            rows.append(self._audit_row(item, outcome, detail, result, len(answered)))
        await self._store(rows)
        missing = [item for key, item in keys.items() if key not in answered]
        if missing and retry_missing:
            for item in missing:
                await self._run_batch([item], retry_missing=False)
        else:
            for item in missing:
                self._degrade(item, "no_llm")

    async def _call(self, keys: dict[str, AdjudicationItem]) -> Any:
        llm, settings = self._llm, self._settings
        if llm is None:
            raise LLMError(LLMErrorKind.disabled, "no llm client configured")
        videos, candidates = [], {}
        for key, item in keys.items():
            shown = adjudication_fields(item.video, settings.adj_description_chars, settings.adj_max_tags)
            videos.append({"video_key": key, **shown})
            candidates[key] = [
                {
                    "topic_id": pair.ref.id,
                    "label": pair.ref.label,
                    "definition": pair.ref.definition,
                    "aliases": [alias.text for alias in pair.ref.aliases if alias.status == "confirmed"][:5],
                }
                for pair in item.pairs
            ]
        topic_ids = sorted({pair.ref.id for item in keys.values() for pair in item.pairs})
        schema = verdict_schema(list(keys), topic_ids)
        candidates_text = json.dumps(candidates, ensure_ascii=False, sort_keys=True)
        videos_text = json.dumps(videos, ensure_ascii=False, sort_keys=True)
        prompt_input = PromptInput(
            trusted=[TrustedBlock(label="candidate_topics", text=candidates_text)],
            untrusted=[UntrustedBlock(label="video_data", source="youtube_metadata", text=videos_text)],
        )
        digest = hashlib.sha256(f"{ADJUDICATION_PROMPT_VERSION}|{candidates_text}|{videos_text}".encode())
        return await call_structured(
            llm,
            task=TaskId.topic_adjudication,
            schema=schema,
            prompt_id="topic_adjudication",
            prompt_input=prompt_input,
            client_id=self._client_id,
            run_id=self._run_id,
            stage="topics",
            guard=self._guard,
            estimated_usd=settings.adj_estimated_usd_per_batch,
            idempotency_key=digest.hexdigest(),
        )

    def _apply(self, item: AdjudicationItem, outcome: _Outcome, detail: dict[str, Any]) -> None:
        settings = self._settings
        shown = adjudication_fields(item.video, settings.adj_description_chars, settings.adj_max_tags)
        by_ref = {pair.ref.id: pair for pair in item.pairs}
        chosen: dict[str, tuple[str, str | None]] = {}
        for match in outcome.matches:
            if match["topic_id"] in by_ref and match["topic_id"] not in chosen:
                found = verify_quote(
                    match["quote"], shown, match.get("field"), settings.adj_quote_min_partial
                )
                chosen[match["topic_id"]] = (match["quote"], found)
        for ref_id, pair in by_ref.items():
            if pair.decision != "pending":
                continue
            if ref_id not in chosen:
                self._policy.llm_rejected(pair, detail)
                continue
            quote, found = chosen[ref_id]
            if found is None:
                self._policy.llm_rejected(pair, detail, unverified_quote=quote)
            else:
                self._policy.llm_accepted(pair, quote, found, detail)

    def _audit_row(
        self, item: AdjudicationItem, outcome: _Outcome, detail: dict[str, Any], result: Any, answered: int
    ) -> dict[str, Any]:
        share = max(answered, 1)
        return {
            "cache_key": item.cache_key,
            "model": detail["model"],
            "prompt_version": detail["prompt_version"],
            "request_id": detail["request_id"],
            "video_ids": [item.video.video_id],
            "verdict": {"outcome": outcome.model_dump(), "detail": detail},
            "tokens_in": result.usage.input_tokens // share,
            "tokens_out": result.usage.output_tokens // share,
            "latency_ms": result.latency_ms,
            "api_data_observed_at": item.observed_at,
            "expires_at": item.expires_at,
        }

    async def _store(self, rows: list[dict[str, Any]]) -> None:
        if not rows or self._uow_factory is None or self._scope is None:
            return
        async with self._uow_factory() as uow:
            await AdjudicationRepository(uow.session, self._scope).put_many(rows)
            await uow.commit()
