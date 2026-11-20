import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import regex
from numpy.typing import NDArray

from app.adapters.embeddings.cache import EmbeddingCache
from app.adapters.embeddings.repository import bytes_to_vector, vector_to_bytes
from app.adapters.embeddings.router import EmbedderRouter, Route
from app.core.config import TopicEngineSettings
from app.core.db import UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.topics import MatchEvidence, PreparedVideoText, TopicRef
from app.ports.embeddings import Embedder, EmbedderUnavailable
from app.services.topics.calibration.background import BackgroundSet
from app.services.topics.matching.base import MatchContext, MatcherDeps, MatcherResult, Pair, matcher_registry
from app.services.topics.repositories import RefEmbeddingRepository
from app.services.topics.text.scripts import dominant_script
from app.services.topics.universe.fingerprint import ref_text

BRAND_MASK = "<brand>"
MAX_TAGS = 10


@dataclass(frozen=True)
class RefVectors:
    model_id: str
    ref_ids: list[str]
    matrix: NDArray[np.float32]
    bg_mu: NDArray[np.float32]
    bg_q95: NDArray[np.float32]
    bg_q99: NDArray[np.float32]

    def index(self) -> dict[str, int]:
        return {ref_id: position for position, ref_id in enumerate(self.ref_ids)}


class RefVectorStore:
    """ref vectors plus background quantiles per model, cached per client by ref fingerprint."""

    def __init__(
        self,
        cache: EmbeddingCache,
        background: BackgroundSet,
        *,
        uow_factory: Callable[[], UnitOfWork] | None = None,
        scope: TenantScope | None = None,
    ) -> None:
        self._cache = cache
        self._background = background
        self._uow_factory = uow_factory
        self._scope = scope
        self._memory: dict[tuple[str, str], tuple[NDArray[np.float32], float, float, float]] = {}

    async def vectors(self, embedder: Embedder, refs: Sequence[TopicRef]) -> RefVectors:
        model_id = embedder.model_id
        missing = [ref for ref in refs if (model_id, ref.fingerprint) not in self._memory]
        if missing and self._uow_factory is not None and self._scope is not None:
            async with self._uow_factory() as uow:
                rows = await RefEmbeddingRepository(uow.session, self._scope).get_many(
                    model_id, [ref.fingerprint for ref in missing]
                )
                for fingerprint, row in rows.items():
                    if row.background_version == self._background.version and row.bg_q95 is not None:
                        vector = bytes_to_vector(row.vector, embedder.dim)
                        self._memory[(model_id, fingerprint)] = (vector, row.bg_mu, row.bg_q95, row.bg_q99)
            missing = [ref for ref in missing if (model_id, ref.fingerprint) not in self._memory]
        if missing:
            await self._compute(embedder, missing)
        entries = [self._memory[(model_id, ref.fingerprint)] for ref in refs]
        dim = embedder.dim
        return RefVectors(
            model_id=model_id,
            ref_ids=[ref.id for ref in refs],
            matrix=np.vstack([entry[0] for entry in entries]) if entries else np.zeros((0, dim), np.float32),
            bg_mu=np.array([entry[1] for entry in entries], dtype=np.float32),
            bg_q95=np.array([entry[2] for entry in entries], dtype=np.float32),
            bg_q99=np.array([entry[3] for entry in entries], dtype=np.float32),
        )

    async def _compute(self, embedder: Embedder, refs: Sequence[TopicRef]) -> None:
        vectors = await self._cache.embed(embedder, [ref_text(ref) for ref in refs], "query")
        background = await self._cache.embed(embedder, list(self._background.titles), "query")
        similarities = vectors @ background.T
        rows = []
        for ref, vector, sims in zip(refs, vectors, similarities, strict=True):
            mu, q95, q99 = (
                float(np.mean(sims)),
                float(np.quantile(sims, 0.95)),
                float(np.quantile(sims, 0.99)),
            )
            self._memory[(embedder.model_id, ref.fingerprint)] = (vector, mu, q95, q99)
            rows.append(
                {
                    "model_id": embedder.model_id,
                    "ref_fingerprint": ref.fingerprint,
                    "vector": vector_to_bytes(vector),
                    "bg_mu": mu,
                    "bg_q95": q95,
                    "bg_q99": q99,
                    "background_version": self._background.version,
                }
            )
        if self._uow_factory is not None and self._scope is not None:
            async with self._uow_factory() as uow:
                await RefEmbeddingRepository(uow.session, self._scope).upsert_many(rows)
                await uow.commit()


def brand_masker(terms: Sequence[str]) -> Callable[[str], str]:
    cleaned = sorted({term.strip() for term in terms if term.strip()}, key=len, reverse=True)
    if not cleaned:
        return lambda text: text
    pattern = regex.compile(
        r"(?<![\p{L}\p{N}])(?:" + "|".join(regex.escape(term) for term in cleaned) + r")(?![\p{L}\p{N}])",
        regex.IGNORECASE,
    )
    return lambda text: pattern.sub(BRAND_MASK, text)


def semantic_inputs(video: PreparedVideoText, mask: Callable[[str], str]) -> tuple[str, str]:
    fields = video.fields
    hashtags = " ".join(fields.hashtags)
    title = mask(fields.title or hashtags or fields.description_head[:120])
    full = " | ".join(
        part
        for part in [fields.title, hashtags, ", ".join(fields.tags[:MAX_TAGS]), fields.description_head]
        if part
    )
    return title, mask(full)


def calibrate(margin: float, model_key: str, settings: TopicEngineSettings) -> float:
    platt = settings.sem_platt.get(model_key)
    if platt is not None:
        a, b = platt
        return 1.0 / (1.0 + math.exp(-(a * margin + b)))
    scale = settings.sem_scale.get(model_key, 0.5)
    return min(1.0, max(0.0, margin / scale))


def band(margin: float, settings: TopicEngineSettings) -> str:
    if margin >= settings.sem_strong_margin:
        return "strong"
    if margin >= settings.sem_ambiguous_margin:
        return "ambiguous"
    return "weak"


@matcher_registry.register("semantic")
class SemanticMatcher:
    """routed multilingual embeddings; scores are margins over each ref's background 95th percentile."""

    name = "semantic"
    order = 20

    def __init__(self, deps: MatcherDeps) -> None:
        self._router: EmbedderRouter | None = deps.router
        self._cache: EmbeddingCache | None = deps.embedding_cache
        self._store: RefVectorStore | None = deps.ref_vectors
        self._settings = deps.settings

    async def score(
        self, videos: Sequence[PreparedVideoText], ctx: MatchContext
    ) -> dict[Pair, MatcherResult]:
        if not ctx.refs:
            return {}
        groups: dict[str, tuple[Route, list[PreparedVideoText]]] = {}
        for video in videos:
            script = dominant_script(video.script_profile) or "Latn"
            route = self._router.route(script) if self._router is not None else None
            if route is None:
                routed = self._router is None or self._router.has_route(script)
                ctx.semantic_missing[video.video_id] = "no_embedder" if routed else "unsupported_script"
                continue
            groups.setdefault(route.model_key, (route, []))[1].append(video)
        results: dict[Pair, MatcherResult] = {}
        mask = brand_masker(ctx.non_topical_terms)
        for model_key, (route, members) in groups.items():
            try:
                results.update(await self._score_group(model_key, route, members, ctx, mask))
            except EmbedderUnavailable:
                if self._router is not None:
                    self._router.mark_unavailable(model_key)
                for video in members:
                    ctx.semantic_missing[video.video_id] = "no_embedder"
        return results

    async def _score_group(
        self,
        model_key: str,
        route: Route,
        videos: list[PreparedVideoText],
        ctx: MatchContext,
        mask: Callable[[str], str],
    ) -> dict[Pair, MatcherResult]:
        if self._cache is None or self._store is None:
            raise EmbedderUnavailable("semantic matching needs an embedding cache and a ref vector store")
        refs = ctx.refs
        ref_vectors = await self._store.vectors(route.embedder, refs)
        inputs = [semantic_inputs(video, mask) for video in videos]
        sources = [video.video_id for video in videos]
        titles = await self._cache.embed(
            route.embedder, [title for title, _ in inputs], "query", source_video_ids=sources
        )
        fulls = await self._cache.embed(
            route.embedder, [full for _, full in inputs], "query", source_video_ids=sources
        )
        cos_title = titles @ ref_vectors.matrix.T
        cos_full = fulls @ ref_vectors.matrix.T
        margins = np.maximum(cos_title, cos_full) - ref_vectors.bg_q95[None, :]
        title_margins = cos_title - ref_vectors.bg_q95[None, :]
        positions = ref_vectors.index()
        settings = self._settings
        results: dict[Pair, MatcherResult] = {}
        for row, video in enumerate(videos):
            order = np.argsort(-margins[row], kind="stable")[: settings.sem_top_k]
            top = {refs[int(column)].id for column in order if margins[row, int(column)] > 0}
            for ref_id in top:
                ctx.add_candidate(video.video_id, ref_id)
            for ref_id in ctx.candidates.get(video.video_id, set()):
                column = positions[ref_id]
                margin = float(margins[row, column])
                value = calibrate(margin, model_key, settings)
                title_value = calibrate(float(title_margins[row, column]), model_key, settings)
                detail = {
                    "model": route.embedder.model_id,
                    "model_key": model_key,
                    "cos_title": round(float(cos_title[row, column]), 4),
                    "cos_full": round(float(cos_full[row, column]), 4),
                    "bg_q95": round(float(ref_vectors.bg_q95[column]), 4),
                    "margin": round(margin, 4),
                    "S": round(value, 4),
                    "S_title": round(title_value, 4),
                    "band": band(margin, settings),
                }
                evidence = MatchEvidence(
                    method="semantic", field="combined", match_quality=round(value, 4), detail=detail
                )
                results[(video.video_id, ref_id)] = MatcherResult(
                    score=value,
                    evidence=[evidence],
                    flags=frozenset({f"semantic_{detail['band']}"}),
                    detail=detail,
                )
        return results
