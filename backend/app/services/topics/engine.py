import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import func, select, text

from app.adapters.embeddings.cache import EmbeddingCache
from app.adapters.embeddings.router import EmbedderRouter
from app.core.config import Settings
from app.core.db import UnitOfWork
from app.core.registry import UnknownRegistrationError
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile, BuyerQuestion
from app.domain.topics import (
    AnswerDirectness,
    AssignedTopic,
    PreparedVideoText,
    TopicMatch,
    TopicRef,
    TopicUniverse,
    VideoTopicAssignment,
)
from app.models.youtube_cache import YtVideo
from app.ports.buyer_questions import BuyerQuestionCandidate
from app.ports.clock import Clock
from app.ports.embeddings import EmbedderUnavailable
from app.ports.llm import LLMClient
from app.services.topics import prompt_specs  # noqa: F401  registers the topic prompts with 10
from app.services.topics.calibration.background import load_background
from app.services.topics.corpus import corpus_frequencies
from app.services.topics.directness.tiebreak import DirectnessTieBreaker, tiebreak_cases
from app.services.topics.lexicon import Lexicons, load_lexicons
from app.services.topics.llm import SpendGuard, UnlimitedSpend
from app.services.topics.matching.base import MatcherDeps, matcher_registry
from app.services.topics.matching.combine import build_combiner
from app.services.topics.matching.semantic import RefVectorStore
from app.services.topics.pipeline import (
    MatchPipeline,
    PipelineOutput,
    RunMode,
    VideoMeta,
    blank_match,
    rerank,
)
from app.services.topics.report import TopicEngineReport
from app.services.topics.repositories import (
    USER_DECISION,
    BoilerplateRepository,
    DirectnessRepository,
    OverrideRepository,
    PreparedTextRepository,
    ReviewRepository,
    TopicMatchRepository,
    TopicUniverseRepository,
)
from app.services.topics.review.flagger import IMPACT_CITED
from app.services.topics.review.queue import OverrideRule, ReviewItemView, TopicMatchOverride, apply_rules
from app.services.topics.text.boilerplate import BoilerplateProfile, ChannelBoilerplateProfiler, ChannelSample
from app.services.topics.text.language import LEXICON_SCRIPTS
from app.services.topics.text.prepare import VideoText, VideoTextPreparer, prepared_hash
from app.services.topics.text.scripts import dominant_script
from app.services.topics.universe.aliases import AliasStatus
from app.services.topics.universe.builder import TopicUniverseBuilder, UniverseInputs, universe_stats
from app.services.topics.universe.expansion.base import CorpusFrequencies
from app.services.topics.universe.expansion.llm_proposer import LLMAliasProposer
from app.services.topics.universe.fingerprint import ref_fingerprint, universe_hash
from app.services.topics.versions import directness_version, engine_version, preparer_version

REFERENCE_SAMPLE_PRIORITY = 0.1


@dataclass(frozen=True)
class UniverseDiff:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UniverseBuild:
    universe: TopicUniverse
    diff: UniverseDiff
    llm_calls: int


@dataclass
class _Loaded:
    text: VideoText
    meta: VideoMeta


class TopicEngine:
    """facade over the topic engine; the pipeline stage and api only talk to this class."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        uow_factory: Callable[[], UnitOfWork],
        *,
        router: EmbedderRouter | None,
        llm: LLMClient | None,
        lexicons: Lexicons | None = None,
    ) -> None:
        self._settings = settings
        self._topics = settings.topics
        self._clock = clock
        self._uow = uow_factory
        self._router = router
        self._llm = llm
        self._lexicons = lexicons or load_lexicons()
        self._cache = EmbeddingCache(uow_factory, memory_size=settings.embeddings.memory_cache_size)
        self._background = load_background()
        self.engine_version = engine_version(self._topics)
        scripts = set(LEXICON_SCRIPTS) | (set(settings.embeddings.routes) if router is not None else set())
        self._preparer = VideoTextPreparer(self._topics, self._lexicons, scripts)
        self._profiler = ChannelBoilerplateProfiler(self._topics)

    @classmethod
    def from_container(cls, container: Any) -> "TopicEngine":
        return cls(
            container.settings,
            container.clock,
            container.uow,
            router=_optional_adapter(container, "embedder"),
            llm=_optional_adapter(container, "llm"),
        )

    @property
    def embedder_model_ids(self) -> dict[str, str]:
        return self._router.model_ids() if self._router is not None else {}

    async def build_universe(
        self,
        scope: TenantScope,
        profile: BusinessProfile,
        buyer_questions: Sequence[BuyerQuestion] = (),
        *,
        extra_questions: Sequence[BuyerQuestionCandidate] = (),
        competitor_names: Sequence[str] = (),
        corpus: CorpusFrequencies | None = None,
        run_id: UUID | None = None,
        guard: SpendGuard | None = None,
    ) -> UniverseBuild:
        scope = scope.for_client(profile.client_id)
        async with self._uow(read_only=True) as uow:
            repository = TopicUniverseRepository(uow.session, scope)
            previous = await repository.load()
            decisions = await repository.alias_decisions()
        proposer = LLMAliasProposer(self._llm, self._topics, guard or UnlimitedSpend()) if self._llm else None
        builder = TopicUniverseBuilder(
            self._topics,
            self._lexicons,
            engine_version=self.engine_version,
            embedder_model_ids=self.embedder_model_ids,
            encoder=self._encode if self._router is not None else None,
            proposer=proposer,
        )
        built = await builder.build(
            UniverseInputs(
                profile=profile,
                buyer_questions=buyer_questions,
                extra_questions=extra_questions,
                competitor_names=competitor_names,
                alias_decisions=decisions,
                previous=previous[0] if previous else None,
                corpus=corpus or CorpusFrequencies(),
            ),
            built_at=self._clock.now(),
            run_id=run_id,
        )
        diff = _diff(previous[0] if previous else None, built.universe)
        rejections = dict(built.rejections)
        if previous:
            rejections.update({key: reason for key, reason in previous[2].items() if reason == USER_DECISION})
        async with self._uow() as uow:
            await TopicUniverseRepository(uow.session, scope).save(
                built.universe, built.non_topical_terms, rejections
            )
            await TopicMatchRepository(uow.session, scope).soft_delete_refs(diff.removed, self._clock.now())
            await uow.commit()
        return UniverseBuild(universe=built.universe, diff=diff, llm_calls=built.llm_calls)

    async def load_universe(
        self, scope: TenantScope, profile_version: int | None = None
    ) -> tuple[TopicUniverse, list[str]] | None:
        async with self._uow(read_only=True) as uow:
            loaded = await TopicUniverseRepository(uow.session, scope).load(profile_version)
        return (loaded[0], loaded[1]) if loaded else None

    def universe_from_refs(
        self, client_id: UUID, profile_version: int, refs: Sequence[TopicRef]
    ) -> TopicUniverse:
        return TopicUniverse(
            client_id=client_id,
            profile_version=profile_version,
            universe_hash=universe_hash(refs, profile_version, self.engine_version, self.embedder_model_ids),
            engine_version=self.engine_version,
            embedder_model_ids=self.embedder_model_ids,
            refs=list(refs),
            stats=universe_stats(refs),
            built_at=self._clock.now(),
        )

    async def decide_alias(
        self, scope: TenantScope, ref_id: str, norm_key: str, status: AliasStatus
    ) -> TopicUniverse:
        async with self._uow(read_only=True) as uow:
            loaded = await TopicUniverseRepository(uow.session, scope).load()
        if loaded is None:
            raise LookupError("this client has no topic universe yet")
        universe, non_topical, rejections = loaded
        refs, found = [], False
        for ref in universe.refs:
            if ref.id == ref_id:
                aliases = []
                for alias in ref.aliases:
                    if alias.norm_key == norm_key and alias.kind != "canonical":
                        found = True
                        weight = 1.0 if status == "confirmed" else alias.weight
                        alias = alias.model_copy(update={"status": status, "weight": weight})
                    aliases.append(alias)
                ref = ref.model_copy(update={"aliases": aliases})
                ref = ref.model_copy(update={"fingerprint": ref_fingerprint(ref)})
            refs.append(ref)
        if not found:
            raise LookupError(f"alias '{norm_key}' is not a reviewable alias of {ref_id}")
        if status == "rejected":
            rejections[(ref_id, norm_key)] = USER_DECISION
        else:
            rejections.pop((ref_id, norm_key), None)
        updated = universe.model_copy(
            update={
                "refs": refs,
                "stats": universe_stats(refs),
                "universe_hash": universe_hash(
                    refs, universe.profile_version, universe.engine_version, universe.embedder_model_ids
                ),
            }
        )
        async with self._uow() as uow:
            await TopicUniverseRepository(uow.session, scope).save(updated, non_topical, rejections)
            await uow.commit()
        return updated

    async def scope_video_ids(self, channel_ids: Sequence[str], as_of: datetime) -> list[str]:
        since = as_of - timedelta(days=self._topics.scope_window_days)
        async with self._uow(read_only=True) as uow:
            statement = (
                select(YtVideo.video_id)
                .where(
                    YtVideo.channel_id.in_(list(channel_ids)),
                    YtVideo.availability == "available",
                    YtVideo.effective_published_at >= since,
                )
                .order_by(YtVideo.effective_published_at.desc())
            )
            return list((await uow.session.execute(statement)).scalars())

    async def match(
        self,
        scope: TenantScope,
        video_ids: Sequence[str],
        *,
        universe: TopicUniverse | None = None,
        non_topical_terms: Sequence[str] | None = None,
        run_id: UUID | None = None,
        mode: RunMode = "cold",
        guard: SpendGuard | None = None,
        own_channel_id: str | None = None,
        window_days: int | None = None,
        locations: Sequence[str] = (),
    ) -> TopicEngineReport:
        timings: dict[str, int] = {}
        started = time.perf_counter()
        now = self._clock.now()
        warnings: list[str] = []
        if universe is None:
            loaded = await self.load_universe(scope)
            if loaded is None:
                raise LookupError("this client has no topic universe yet")
            universe, loaded_terms = loaded
            non_topical_terms = non_topical_terms if non_topical_terms is not None else loaded_terms
        terms = list(non_topical_terms or [])
        window_start = now - timedelta(days=window_days) if window_days else None
        async with self._uow() as uow:
            videos = await self._load_videos(uow, video_ids, own_channel_id, window_start)
            profiles = await self._boilerplate(uow, {item.text.channel_id for item in videos.values()}, now)
            prepared = await self._prepare(uow, videos, profiles, now)
            existing = await TopicMatchRepository(uow.session, scope).current_keys(list(prepared))
            override_rows = await OverrideRepository(uow.session, scope).list_for_videos(list(prepared))
            overrides, stale = self._override_rules(override_rows, videos)
            await uow.commit()
        timings["prepare"] = _elapsed(started)
        todo = [
            item
            for video_id, item in prepared.items()
            if existing.get(video_id) != {(universe.universe_hash, item.prepared_hash, self.engine_version)}
        ]
        pipeline = self._pipeline(scope, guard or UnlimitedSpend(), locations)
        stage_started = time.perf_counter()
        output = await pipeline.run(
            universe,
            terms,
            todo,
            {video_id: videos[video_id].meta for video_id in prepared},
            corpus_frequencies(prepared.values()),
            mode=mode,
            now=now,
            run_id=run_id,
            overrides=overrides,
        )
        timings["match"] = _elapsed(stage_started)
        persist_started = time.perf_counter()
        await self._persist(scope, universe, todo, output, videos, stale, now)
        timings["persist"] = _elapsed(persist_started)
        timings["total"] = _elapsed(started)
        if "thin_universe" in universe.warnings:
            warnings.append("thin_universe")
        return self._report(scope, run_id, universe, prepared, todo, videos, output, timings, warnings)

    async def matches(self, scope: TenantScope, video_ids: Sequence[str]) -> list[TopicMatch]:
        async with self._uow(read_only=True) as uow:
            return await TopicMatchRepository(uow.session, scope).list_for_videos(video_ids)

    async def assignments(self, scope: TenantScope, video_ids: Sequence[str]) -> list[VideoTopicAssignment]:
        async with self._uow(read_only=True) as uow:
            rows = await TopicMatchRepository(uow.session, scope).list_for_videos(video_ids)
            none = await TopicMatchRepository(uow.session, scope).adjudicated_none(video_ids)
            prepared = await PreparedTextRepository(uow.session).get_many(video_ids)
        by_video: dict[str, list[TopicMatch]] = {}
        for match in rows:
            by_video.setdefault(match.video_id, []).append(match)
        assignments = []
        for video_id in video_ids:
            matches = by_video.get(video_id, [])
            accepted = [match for match in matches if match.decision == "accepted"]
            flags = prepared[video_id].quality_flags if video_id in prepared else []
            reason = None
            if not accepted:
                if any(match.decision == "unadjudicated" for match in matches):
                    reason = "unadjudicated"
                elif video_id in none:
                    reason = "adjudicated_none"
                elif "unsupported_script" in flags:
                    reason = "unsupported_script"
                elif "no_topical_text" in flags:
                    reason = "no_topical_text"
                else:
                    reason = "below_threshold"
            assignments.append(
                VideoTopicAssignment(
                    video_id=video_id,
                    client_id=scope.client_id,
                    universe_hash=matches[0].universe_hash if matches else "",
                    topics=[
                        AssignedTopic(
                            topic_ref_id=match.topic_ref_id,
                            kind=match.topic_kind,
                            confidence=match.confidence,
                            combined_score=match.combined_score,
                            is_primary=match.is_primary,
                        )
                        for match in accepted
                    ],
                    primary_topic_id=next(
                        (match.topic_ref_id for match in accepted if match.is_primary), None
                    ),
                    unmatched_reason=reason,
                )
            )
        return assignments

    async def answer_directness(self, scope: TenantScope, video_ids: Sequence[str]) -> list[AnswerDirectness]:
        async with self._uow(read_only=True) as uow:
            return await DirectnessRepository(uow.session, scope).list_for_videos(video_ids)

    async def request_tiebreak(
        self,
        scope: TenantScope,
        pairs: Sequence[tuple[str, str]],
        *,
        run_id: UUID | None = None,
        guard: SpendGuard | None = None,
    ) -> list[AnswerDirectness]:
        video_ids = sorted({video_id for video_id, _ in pairs})
        wanted = set(pairs)
        async with self._uow(read_only=True) as uow:
            rows = await DirectnessRepository(uow.session, scope).list_for_videos(video_ids)
            prepared = await PreparedTextRepository(uow.session).get_many(video_ids)
            loaded = await TopicUniverseRepository(uow.session, scope).load()
            observed = await self._observed(uow, video_ids)
        rows = [row for row in rows if (row.video_id, row.buyer_question_ref_id) in wanted]
        if self._llm is None or loaded is None:
            return rows
        refs = {ref.id: ref for ref in loaded[0].refs}
        cases = tiebreak_cases(rows, prepared, refs, self._topics.tiebreak_band)
        breaker = DirectnessTieBreaker(self._llm, self._topics, guard or UnlimitedSpend())
        resolved = await breaker.resolve(cases, client_id=scope.client_id, run_id=run_id)
        if resolved:
            now = self._clock.now()
            async with self._uow() as uow:
                repository = DirectnessRepository(uow.session, scope)
                for row in resolved:
                    observed_at = observed[row.video_id]
                    await repository.upsert(
                        row,
                        refs[row.buyer_question_ref_id].fingerprint,
                        directness_version(),
                        now,
                        observed_at,
                        self._expiry(observed_at),
                    )
                await uow.commit()
        by_pair = {(row.video_id, row.buyer_question_ref_id): row for row in resolved}
        return [by_pair.get((row.video_id, row.buyer_question_ref_id), row) for row in rows]

    async def prioritise_review(self, scope: TenantScope, video_ids: Sequence[str]) -> int:
        # videos cited by a shown recommendation: raise open items and sample the rest once
        async with self._uow() as uow:
            reviews = ReviewRepository(uow.session, scope)
            raised = await reviews.raise_priority(video_ids, IMPACT_CITED, "reference_video_sample")
            matches = await TopicMatchRepository(uow.session, scope).list_for_videos(video_ids)
            open_pairs = {
                (item.video_id, item.topic_ref_id) for item in await reviews.open_items(limit=10_000)
            }
            reviewed = await reviews.reviewed_pairs(video_ids)
            fresh = [
                {
                    "video_id": match.video_id,
                    "topic_ref_id": match.topic_ref_id,
                    "reasons": ["reference_video_sample"],
                    "priority": REFERENCE_SAMPLE_PRIORITY,
                    "status": "open",
                }
                for match in matches
                if match.decision == "accepted"
                and (match.video_id, match.topic_ref_id) not in open_pairs | reviewed
            ]
            await reviews.add_items(fresh)
            await uow.commit()
        return raised + len(fresh)

    async def review_queue(self, scope: TenantScope, *, limit: int = 50) -> list[ReviewItemView]:
        async with self._uow(read_only=True) as uow:
            items = await ReviewRepository(uow.session, scope).open_items(limit=limit)
            matches = await TopicMatchRepository(uow.session, scope).list_for_videos(
                [item.video_id for item in items]
            )
            loaded = await TopicUniverseRepository(uow.session, scope).load()
            labels = {ref.id: ref.label for ref in loaded[0].refs} if loaded else {}
            by_pair = {(match.video_id, match.topic_ref_id): match for match in matches}
            return [
                ReviewItemView(
                    video_id=item.video_id,
                    topic_ref_id=item.topic_ref_id,
                    ref_label=labels.get(item.topic_ref_id, item.topic_ref_id),
                    reasons=list(item.reasons),
                    priority=item.priority,
                    match=by_pair.get((item.video_id, item.topic_ref_id)),
                )
                for item in items
            ]

    async def apply_override(self, scope: TenantScope, override: TopicMatchOverride) -> TopicMatch:
        scope = scope.for_client(override.client_id)
        now = self._clock.now()
        async with self._uow() as uow:
            loaded = await TopicUniverseRepository(uow.session, scope).load()
            if loaded is None:
                raise LookupError("this client has no topic universe yet")
            universe = loaded[0]
            refs = {ref.id: ref for ref in universe.refs}
            targets = [
                override.topic_ref_id,
                *([override.reassigned_to_ref_id] if override.verdict == "reassign" else []),
            ]
            if any(ref_id not in refs for ref_id in targets if ref_id) or (
                override.verdict == "reassign"
                and (override.reassigned_to_ref_id in {None, override.topic_ref_id})
            ):
                raise ValueError("overrides may only choose among this client's existing topics")
            video = (
                await uow.session.execute(select(YtVideo).where(YtVideo.video_id == override.video_id))
            ).scalar_one_or_none()
            prepared = (await PreparedTextRepository(uow.session).get_many([override.video_id])).get(
                override.video_id
            )
            if video is None or prepared is None:
                raise LookupError(f"video {override.video_id} has not been prepared for topic matching")
            await OverrideRepository(uow.session, scope).upsert(
                {
                    "video_id": override.video_id,
                    "topic_ref_id": override.topic_ref_id,
                    "verdict": override.verdict,
                    "reassigned_to_ref_id": override.reassigned_to_ref_id,
                    "reviewer_id": override.reviewer_id,
                    "reason": override.reason,
                    "content_hash_at_review": video.content_hash or "",
                    "ids_verified_at": now,
                }
            )
            matches_repo = TopicMatchRepository(uow.session, scope)
            current = [
                match
                for match in await matches_repo.list_for_videos([override.video_id])
                if match.universe_hash == universe.universe_hash
            ]
            rule = OverrideRule(
                topic_ref_id=override.topic_ref_id,
                verdict=override.verdict,
                reassigned_to_ref_id=override.reassigned_to_ref_id,
                reviewer_id=override.reviewer_id,
            )

            def blank(ref_id: str) -> TopicMatch:
                return blank_match(refs[ref_id], prepared, universe, self.engine_version, now)

            updated = rerank(apply_rules(current, [rule], refs, blank, now), refs)
            observed = video.metadata_fetched_at
            for match in updated:
                trace = await matches_repo.get_trace(match.video_id, match.topic_ref_id)
                if match.topic_ref_id in targets:
                    trace = {**trace, "override": {"verdict": override.verdict, "at": now.isoformat()}}
                await matches_repo.upsert_one(match, trace, observed, self._expiry(observed))
            await ReviewRepository(uow.session, scope).resolve(
                override.video_id, override.topic_ref_id, override.verdict, now
            )
            await uow.commit()
        return next(match for match in updated if match.topic_ref_id == override.topic_ref_id)

    async def similar_refs(self, scope: TenantScope, ref_id: str, k: int = 5) -> list[tuple[str, float]]:
        loaded = await self.load_universe(scope)
        route = self._router.route("Latn") if self._router is not None else None
        if loaded is None or route is None:
            return []
        refs = [ref for ref in loaded[0].refs if ref.status == "active"]
        store = RefVectorStore(self._cache, self._background, uow_factory=self._uow, scope=scope)
        vectors = await store.vectors(route.embedder, refs)
        index = vectors.index()
        if ref_id not in index:
            return []
        scores = vectors.matrix @ vectors.matrix[index[ref_id]]
        order = [
            position for position in np.argsort(-scores, kind="stable") if vectors.ref_ids[position] != ref_id
        ]
        return [(vectors.ref_ids[position], round(float(scores[position]), 4)) for position in order[:k]]

    def _pipeline(self, scope: TenantScope, guard: SpendGuard, locations: Sequence[str]) -> MatchPipeline:
        deps = MatcherDeps(
            settings=self._topics,
            lexicons=self._lexicons,
            router=self._router,
            embedding_cache=self._cache,
            ref_vectors=RefVectorStore(self._cache, self._background, uow_factory=self._uow, scope=scope),
        )
        matchers = [matcher_registry.get(name)(deps) for name in matcher_registry]
        return MatchPipeline(
            self._topics,
            self._lexicons,
            matchers,
            build_combiner(self._topics),
            engine_version=self.engine_version,
            llm=self._llm,
            guard=guard,
            model_key=self._settings.llm.anthropic.low.model or "low",
            locations=locations,
            uow_factory=self._uow,
            scope=scope,
        )

    async def _encode(self, texts: Sequence[str]) -> NDArray[np.float32] | None:
        route = self._router.route("Latn") if self._router is not None else None
        if route is None or not texts:
            return None
        try:
            return await self._cache.embed(route.embedder, list(texts), "query")
        except EmbedderUnavailable:
            return None

    def _expiry(self, observed: datetime) -> datetime:
        return observed + timedelta(days=self._settings.policy.retention_days)

    async def _observed(self, uow: UnitOfWork, video_ids: Sequence[str]) -> dict[str, datetime]:
        statement = select(YtVideo.video_id, YtVideo.metadata_fetched_at).where(
            YtVideo.video_id.in_(list(video_ids))
        )
        return {video_id: fetched for video_id, fetched in (await uow.session.execute(statement)).all()}

    async def _load_videos(
        self,
        uow: UnitOfWork,
        video_ids: Sequence[str],
        own_channel_id: str | None,
        window_start: datetime | None,
    ) -> dict[str, _Loaded]:
        if not video_ids:
            return {}
        statement = select(YtVideo).where(
            YtVideo.video_id.in_(list(video_ids)), YtVideo.availability == "available"
        )
        loaded = {}
        for row in (await uow.session.execute(statement)).scalars():
            observed = row.metadata_fetched_at
            text_item = VideoText(
                video_id=row.video_id,
                channel_id=row.channel_id,
                title=row.title or "",
                description=row.description or "",
                tags=list(row.tags or []),
                content_hash=row.content_hash or _content_hash(row),
                default_language=row.default_language,
                is_short=row.format == "short_form",
            )
            loaded[row.video_id] = _Loaded(
                text=text_item,
                meta=VideoMeta(
                    video_id=row.video_id,
                    channel_id=row.channel_id,
                    published_at=row.effective_published_at,
                    observed_at=observed,
                    expires_at=self._expiry(observed),
                    is_short=text_item.is_short,
                    own=row.channel_id == own_channel_id,
                    in_window=window_start is None or row.effective_published_at >= window_start,
                ),
            )
        return loaded

    async def _boilerplate(
        self, uow: UnitOfWork, channel_ids: set[str], now: datetime
    ) -> dict[str, BoilerplateProfile]:
        repository = BoilerplateRepository(uow.session)
        stored = await repository.get_many(sorted(channel_ids))
        settings = self._topics
        stale_before = now - timedelta(days=self._settings.policy.retention_days)
        profiles = {}
        for channel_id in sorted(channel_ids):
            current = stored.get(channel_id)
            if current is not None and current[1] > stale_before:
                new_uploads = await uow.session.scalar(
                    select(func.count()).where(
                        YtVideo.channel_id == channel_id, YtVideo.published_at > current[1]
                    )
                )
                if (new_uploads or 0) < settings.bp_refresh_new_uploads:
                    profiles[channel_id] = current[0]
                    continue
            statement = (
                select(YtVideo.title, YtVideo.description, YtVideo.tags)
                .where(YtVideo.channel_id == channel_id, YtVideo.availability == "available")
                .order_by(YtVideo.effective_published_at.desc())
                .limit(settings.bp_sample)
            )
            samples = [
                ChannelSample(description=description or "", tags=list(tags or []), title=title or "")
                for title, description, tags in (await uow.session.execute(statement)).all()
            ]
            profile = self._profiler.build(channel_id, samples)
            await repository.upsert(profile, now)
            profiles[channel_id] = profile
        return profiles

    async def _prepare(
        self,
        uow: UnitOfWork,
        videos: dict[str, _Loaded],
        profiles: dict[str, BoilerplateProfile],
        now: datetime,
    ) -> dict[str, PreparedVideoText]:
        repository = PreparedTextRepository(uow.session)
        stored = await repository.get_many(list(videos))
        prepared, fresh, versions = {}, [], {}
        for video_id, item in videos.items():
            profile = profiles.get(item.text.channel_id) or BoilerplateProfile.unprofiled(
                item.text.channel_id
            )
            expected = prepared_hash(item.text.content_hash, profile.version)
            current = stored.get(video_id)
            if current is not None and current.prepared_hash == expected:
                prepared[video_id] = current
                continue
            prepared[video_id] = self._preparer.prepare(item.text, profile)
            fresh.append(prepared[video_id])
            versions[video_id] = profile.version
        if fresh:
            await repository.upsert_many(fresh, versions, preparer_version(), now)
        return prepared

    def _override_rules(
        self, rows: Sequence[Any], videos: dict[str, _Loaded]
    ) -> tuple[dict[str, list[OverrideRule]], dict[str, str]]:
        rules: dict[str, list[OverrideRule]] = {}
        stale: dict[str, str] = {}
        for row in rows:
            content_hash = videos[row.video_id].text.content_hash if row.video_id in videos else None
            if row.stale or content_hash is None:
                continue
            if row.content_hash_at_review != content_hash:
                stale[row.video_id] = content_hash
                continue
            rules.setdefault(row.video_id, []).append(
                OverrideRule(
                    topic_ref_id=row.topic_ref_id,
                    verdict=row.verdict,
                    reassigned_to_ref_id=row.reassigned_to_ref_id,
                    reviewer_id=row.reviewer_id,
                )
            )
        return rules, stale

    async def _persist(
        self,
        scope: TenantScope,
        universe: TopicUniverse,
        todo: Sequence[PreparedVideoText],
        output: PipelineOutput,
        videos: dict[str, _Loaded],
        stale: dict[str, str],
        now: datetime,
    ) -> None:
        video_ids = [video.video_id for video in todo]
        observed = {video_id: videos[video_id].meta.observed_at for video_id in video_ids}
        expires = {video_id: videos[video_id].meta.expires_at for video_id in video_ids}
        fingerprints = {ref.id: ref.fingerprint for ref in universe.refs}
        async with self._uow() as uow:
            await uow.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"{scope.client_id}:topics"}
            )
            await TopicMatchRepository(uow.session, scope).replace_for_videos(
                video_ids, output.matches, output.traces, observed, expires
            )
            await DirectnessRepository(uow.session, scope).replace_for_videos(
                video_ids, output.directness, fingerprints, directness_version(), now, observed, expires
            )
            await ReviewRepository(uow.session, scope).sync_open(video_ids, output.review_items)
            if stale:
                await OverrideRepository(uow.session, scope).mark_stale(stale)
            await uow.commit()

    def _report(
        self,
        scope: TenantScope,
        run_id: UUID | None,
        universe: TopicUniverse,
        prepared: dict[str, PreparedVideoText],
        todo: Sequence[PreparedVideoText],
        videos: dict[str, _Loaded],
        output: PipelineOutput,
        timings: dict[str, int],
        warnings: list[str],
    ) -> TopicEngineReport:
        accepted = [match for match in output.matches if match.decision == "accepted"]
        matched = {match.video_id for match in accepted}
        per_channel: dict[str, list[bool]] = {}
        for video in todo:
            per_channel.setdefault(videos[video.video_id].meta.channel_id, []).append(
                video.video_id in matched
            )
        unmatched = [assignment for assignment in output.assignments if assignment.unmatched_reason]
        scripts = Counter(
            dominant_script(prepared[assignment.video_id].script_profile) or "none"
            for assignment in unmatched
        )
        if output.unsupported_videos:
            warnings.append(f"unsupported_script_videos:{len(output.unsupported_videos)}")
        return TopicEngineReport(
            client_id=scope.client_id,
            run_id=run_id,
            universe_hash=universe.universe_hash,
            engine_version=self.engine_version,
            videos_considered=len(prepared),
            videos_matched=len(matched),
            videos_reused=len(prepared) - len(todo),
            coverage_by_channel={
                channel: round(sum(flags) / len(flags), 4) for channel, flags in sorted(per_channel.items())
            },
            matches_by_method=dict(Counter(match.method.value for match in accepted)),
            matches_by_confidence=dict(Counter(match.confidence for match in accepted)),
            pairs_implicitly_rejected=output.implicit_rejections,
            review_items_created=len(output.review_items),
            directness_rows=len(output.directness),
            llm_calls=output.adjudication.calls,
            llm_tokens_in=output.adjudication.tokens_in,
            llm_tokens_out=output.adjudication.tokens_out,
            llm_cost_usd=round(output.adjudication.cost_usd, 6),
            adjudication_cache_hits=output.adjudication.cache_hits,
            embed_cache_hit_rate=round(self._cache.hit_rate, 4),
            unmatched_by_reason=dict(Counter(assignment.unmatched_reason for assignment in unmatched)),
            unmatched_by_script=dict(scripts),
            timings_ms=timings,
            degraded_modes=sorted(output.degraded),
            warnings=warnings,
        )


def _optional_adapter(container: Any, port: str) -> Any:
    try:
        return container.adapter(port)
    except (LookupError, UnknownRegistrationError):
        return None


def _diff(previous: TopicUniverse | None, current: TopicUniverse) -> UniverseDiff:
    old = {ref.id: ref.fingerprint for ref in previous.refs if ref.status == "active"} if previous else {}
    new = {ref.id: ref.fingerprint for ref in current.refs if ref.status == "active"}
    return UniverseDiff(
        added=sorted(set(new) - set(old)),
        changed=sorted(ref_id for ref_id in set(new) & set(old) if new[ref_id] != old[ref_id]),
        removed=sorted(set(old) - set(new)),
    )


def _content_hash(row: YtVideo) -> str:
    raw = json.dumps(
        [row.title, row.description, list(row.tags or []), row.default_language], ensure_ascii=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
