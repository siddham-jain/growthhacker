import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from app.domain.profile import FactKind
from app.domain.web import ExtractionKind, SourceKind
from app.ports.llm import PayloadClass, PromptInput, TaskId, TrustedBlock, UntrustedBlock
from app.services.website_intel.extract.injection import contains_instruction
from app.services.website_intel.inputs.urls import path_of
from app.services.website_intel.models import ExtractedPage
from app.services.website_intel.profile.deterministic import DeterministicTables
from app.services.website_intel.profile.evidence import EvidenceVerifier, FactStore
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.prompts import prompt_version
from app.services.website_intel.profile.schemas import LlmFact, PageFactsOutput

EXTRACTOR_VERSION = "1"
MAX_PAGES_PER_CALL = 4
MAX_BATCH_CHARS = 48_000
HEAD_CHARS = 8_000
DROP_RATE_LIMIT = 0.3
MAX_INJECTION_FLAGS = 3
MAX_CANDIDATES_PER_PAGE = 30
STRICT_NOTE = (
    "A previous attempt quoted text that is not on the page. Copy quotes exactly, character for character, "
    "and leave out any fact you cannot quote exactly."
)
_HINT_LINE = re.compile(r"(₹|rs\.?|\$|€|£|price|cost|fee|book|appointment|call|faq|\?)", re.IGNORECASE)


class MapCache(Protocol):
    async def get(
        self, content_hash: str, extractor_version: str, prompt_version: str
    ) -> list[dict[str, Any]] | None: ...

    async def put(
        self,
        page: ExtractedPage,
        *,
        extractor_version: str,
        prompt_version: str,
        model: str,
        facts: list[dict[str, Any]],
        verified: int,
        dropped: int,
    ) -> None: ...


def llm_view(text: str, headings: Sequence[str], cap: int) -> tuple[str, bool]:
    """headings outline, the first 8k chars, then price/cta/faq paragraphs up to the cap (plan 03 §5.7.10)."""
    if len(text) <= cap:
        return text, False
    outline = "\n".join(f"- {heading}" for heading in headings[:40])
    head = text[:HEAD_CHARS]
    parts = [outline, head]
    size = len(outline) + len(head)
    for paragraph in text[HEAD_CHARS:].split("\n"):
        if size >= cap:
            break
        if _HINT_LINE.search(paragraph):
            parts.append(paragraph)
            size += len(paragraph) + 1
    return "\n".join(part for part in parts if part)[:cap], True


@dataclass
class MapReport:
    verified: int = 0
    dropped: int = 0
    low_trust: list[UUID] = field(default_factory=list)
    excluded_for_injection: list[UUID] = field(default_factory=list)
    llm_failed: list[UUID] = field(default_factory=list)
    cached: int = 0
    truncated: int = 0


@dataclass
class _PageJob:
    key: str
    page: ExtractedPage
    text: str
    cta_ids: dict[str, str]
    price_ids: set[str]


class PageFactExtractor:
    """map step on the low tier with verbatim-quote verification and a per-page trust gate (plan 03 §5.9)."""

    def __init__(
        self,
        llm: LlmGateway,
        verifier: EvidenceVerifier,
        *,
        char_cap: int,
        concurrency: int = 8,
        cache: MapCache | None = None,
    ) -> None:
        self._llm = llm
        self._verifier = verifier
        self._char_cap = char_cap
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cache = cache
        self._prompt_version = prompt_version(TaskId.website_page_map.value)

    async def extract(
        self,
        pages: Sequence[ExtractedPage],
        texts: dict[UUID, str],
        tables: DeterministicTables,
        store: FactStore,
    ) -> MapReport:
        report = MapReport()
        jobs: list[_PageJob] = []
        for index, page in enumerate(pages):
            if page.page.injection_flags > MAX_INJECTION_FLAGS:
                report.excluded_for_injection.append(page.page.page_id)
                continue
            if self._cache is not None:
                cached = await self._cache.get(
                    page.page.content_hash, EXTRACTOR_VERSION, self._prompt_version
                )
                if cached is not None:
                    report.cached += 1
                    for item in cached:
                        self._store(
                            store,
                            page,
                            LlmFact.model_validate(item["fact"]),
                            item["score"],
                            item.get("char_start"),
                        )
                    report.verified += len(cached)
                    continue
            view, truncated = llm_view(
                texts.get(page.page.page_id, page.page.text), page.page.headings, self._char_cap
            )
            report.truncated += int(truncated)
            cta_ids = dict(
                list(
                    {
                        row.cta_id: key for key, row in tables.ctas.items() if page.page.url in row.pages
                    }.items()
                )[:MAX_CANDIDATES_PER_PAGE]
            )
            price_ids = {
                row.price_id for row in tables.prices.values() if row.candidate.page_url == page.page.url
            }
            jobs.append(_PageJob(f"P{index + 1}", page, view, cta_ids, price_ids))
        if not self._llm.available:
            report.llm_failed.extend(job.page.page.page_id for job in jobs)
            return report
        batches = self._batches(jobs)
        results = await asyncio.gather(*(self._run(batch, tables, strict=False) for batch in batches))
        retry: list[_PageJob] = []
        for batch, output in zip(batches, results, strict=True):
            for job in batch:
                retry.extend(await self._accept(job, output, tables, store, report, final=False))
        if retry:
            outputs = await asyncio.gather(*(self._run([job], tables, strict=True) for job in retry))
            for job, output in zip(retry, outputs, strict=True):
                await self._accept(job, output, tables, store, report, final=True)
        return report

    def _batches(self, jobs: list[_PageJob]) -> list[list[_PageJob]]:
        batches: list[list[_PageJob]] = []
        current: list[_PageJob] = []
        size = 0
        for job in jobs:
            if current and (len(current) >= MAX_PAGES_PER_CALL or size + len(job.text) > MAX_BATCH_CHARS):
                batches.append(current)
                current, size = [], 0
            current.append(job)
            size += len(job.text)
        if current:
            batches.append(current)
        return batches

    async def _run(
        self, batch: list[_PageJob], tables: DeterministicTables, *, strict: bool
    ) -> PageFactsOutput | None:
        untrusted = []
        for job in batch:
            ctas = "\n".join(
                f"{cta_id}: {tables.ctas[key].candidate.type} | {tables.ctas[key].candidate.label[:60]}"
                for cta_id, key in job.cta_ids.items()
            )
            prices = "\n".join(
                f"{price_id}: {tables.prices[price_id].candidate.raw_text} | "
                f"{tables.prices[price_id].candidate.heading or ''}"
                for price_id in sorted(job.price_ids)
            )
            untrusted.append(
                UntrustedBlock(
                    label=job.key,
                    source=path_of(job.page.page.url),
                    text=f"{job.text}\n\nCALL-TO-ACTION CANDIDATES:\n{ctas or 'none'}\n\nPRICE CANDIDATES:\n{prices or 'none'}",
                )
            )
        listing = "\n".join(
            f"{job.key}: path={path_of(job.page.page.url)} type_guess={job.page.page.page_type} "
            f"lang={job.page.page.lang or 'unknown'} source={job.page.page.source_kind.value}"
            for job in batch
        )
        trusted = [TrustedBlock(label="pages", text=listing)]
        if strict:
            trusted.append(TrustedBlock(label="retry", text=STRICT_NOTE))
        payload = frozenset(
            {
                PayloadClass.public_youtube
                if job.page.page.source_kind is SourceKind.youtube_channel
                else PayloadClass.public_web
                for job in batch
            }
        )
        async with self._semaphore:
            return await self._llm.structured(
                TaskId.website_page_map,
                PageFactsOutput,
                PromptInput(trusted=trusted, untrusted=untrusted),
                payload=payload,
                max_output_tokens=4096,
            )

    async def _accept(
        self,
        job: _PageJob,
        output: PageFactsOutput | None,
        tables: DeterministicTables,
        store: FactStore,
        report: MapReport,
        *,
        final: bool,
    ) -> list[_PageJob]:
        if output is None:
            report.llm_failed.append(job.page.page.page_id)
            return []
        page_facts = next((item for item in output.pages if item.page_id == job.key), None)
        if page_facts is None or not page_facts.facts:
            return []
        accepted: list[tuple[LlmFact, float, int | None]] = []
        for fact in page_facts.facts:
            checked = self._check(fact, job)
            if checked is not None:
                accepted.append((fact, *checked))
        dropped = len(page_facts.facts) - len(accepted)
        if dropped / len(page_facts.facts) > DROP_RATE_LIMIT:
            if not final:
                return [job]
            report.low_trust.append(job.page.page.page_id)
            report.dropped += len(page_facts.facts)
            store.dropped += len(page_facts.facts)
            return []
        report.verified += len(accepted)
        report.dropped += dropped
        store.dropped += dropped
        for fact, score, char_start in accepted:
            self._store(store, job.page, fact, score, char_start, job.cta_ids)
        if self._cache is not None:
            await self._cache.put(
                job.page,
                extractor_version=EXTRACTOR_VERSION,
                prompt_version=self._prompt_version,
                model=self._llm.usage.models.get(TaskId.website_page_map.value, "unknown"),
                facts=[
                    {"fact": fact.model_dump(), "score": score, "char_start": char_start}
                    for fact, score, char_start in accepted
                ],
                verified=len(accepted),
                dropped=dropped,
            )
        return []

    def _check(self, fact: LlmFact, job: _PageJob) -> tuple[float, int | None] | None:
        if contains_instruction(fact.text) or contains_instruction(fact.quote):
            return None
        attrs = {attr.key: attr.value for attr in fact.attrs}
        if "cta_id" in attrs and attrs["cta_id"] not in job.cta_ids:
            return None
        if "price_id" in attrs and attrs["price_id"] not in job.price_ids:
            return None
        verification = self._verifier.verify(fact.text, fact.quote, job.page.page.text)
        if verification is None:
            return None
        return verification.score, verification.char_start

    @staticmethod
    def _store(
        store: FactStore,
        page: ExtractedPage,
        fact: LlmFact,
        score: float,
        char_start: int | None,
        cta_ids: dict[str, str] | None = None,
    ) -> None:
        attrs = {attr.key: attr.value[:300] for attr in fact.attrs}
        if cta_ids and attrs.get("cta_id") in cta_ids:
            attrs["cta_key"] = cta_ids[attrs["cta_id"]]
        extraction = (
            ExtractionKind.channel_metadata
            if page.page.source_kind is SourceKind.youtube_channel
            else ExtractionKind.page_text
        )
        store.add(
            page,
            kind=FactKind(fact.kind),
            text=fact.text,
            quote=fact.quote,
            extraction=extraction,
            score=score,
            char_start=char_start,
            attrs=attrs,
        )
