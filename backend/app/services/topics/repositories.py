from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.topics import (
    AnswerDirectness,
    PreparedFields,
    PreparedVideoText,
    RemovedSpan,
    TopicAlias,
    TopicMatch,
    TopicRef,
    TopicUniverse,
    TopicUniverseStats,
)
from app.models import topics as orm
from app.repositories.shared.base import SharedRepository, chunked
from app.repositories.tenant.base import TenantRepository
from app.services.topics.text.boilerplate import BoilerplateProfile

ALIAS_COLUMNS = (
    "text", "norm_key", "phonetic_key", "lang", "script", "kind", "origin", "status", "anchor_text",
    "match_mode", "weight", "ambiguous",
)  # fmt: skip
USER_DECISION = "user"


class TopicUniverseRepository(TenantRepository[orm.TopicUniverse]):
    model = orm.TopicUniverse

    async def save(
        self,
        universe: TopicUniverse,
        non_topical_terms: Sequence[str],
        rejections: Mapping[tuple[str, str], str],
    ) -> None:
        scope = {"workspace_id": self.scope.workspace_id, "client_id": universe.client_id}
        values = {
            **scope,
            "profile_version": universe.profile_version,
            "universe_hash": universe.universe_hash,
            "engine_version": universe.engine_version,
            "embedder_model_ids": universe.embedder_model_ids,
            "stats": {**universe.stats.model_dump(), "non_topical_terms": list(non_topical_terms)},
            "warnings": universe.warnings,
            "built_at": universe.built_at,
        }
        statement = insert(orm.TopicUniverse).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "profile_version"],
            set_={
                key: statement.excluded[key] for key in values if key not in {"client_id", "profile_version"}
            },
        )
        await self.session.execute(statement)
        await self.session.execute(
            delete(orm.TopicRef).where(
                orm.TopicRef.workspace_id == self.scope.workspace_id,
                orm.TopicRef.client_id == universe.client_id,
                orm.TopicRef.profile_version == universe.profile_version,
            )
        )
        ref_rows = [
            {
                **scope,
                "profile_version": ref.profile_version,
                **ref.model_dump(
                    mode="json", exclude={"aliases", "client_id", "profile_version", "expires_at"}
                ),
                "expires_at": ref.expires_at,
            }
            for ref in universe.refs
        ]
        alias_rows = [
            {
                **scope,
                "topic_ref_id": ref.id,
                "profile_version": ref.profile_version,
                **{column: getattr(alias, column) for column in ALIAS_COLUMNS},
                "rejected_reason": rejections.get((ref.id, alias.norm_key))
                if alias.status == "rejected"
                else None,
            }
            for ref in universe.refs
            for alias in ref.aliases
        ]
        for table, rows in ((orm.TopicRef.__table__, ref_rows), (orm.TopicAlias.__table__, alias_rows)):
            for chunk in chunked(rows, len(table.columns)):
                await self.session.execute(insert(table).values(list(chunk)))

    async def load(self, profile_version: int | None = None) -> tuple[TopicUniverse, list[str], dict] | None:
        statement = self._scoped(select(orm.TopicUniverse))
        if profile_version is None:
            statement = statement.order_by(orm.TopicUniverse.profile_version.desc()).limit(1)
        else:
            statement = statement.where(orm.TopicUniverse.profile_version == profile_version)
        row = (await self.session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        ref_rows = (
            await self.session.execute(
                select(orm.TopicRef)
                .where(
                    orm.TopicRef.client_id == row.client_id,
                    orm.TopicRef.profile_version == row.profile_version,
                )
                .order_by(orm.TopicRef.id)
            )
        ).scalars()
        alias_rows = (
            await self.session.execute(
                select(orm.TopicAlias)
                .where(
                    orm.TopicAlias.client_id == row.client_id,
                    orm.TopicAlias.profile_version == row.profile_version,
                )
                .order_by(orm.TopicAlias.topic_ref_id, orm.TopicAlias.norm_key)
            )
        ).scalars()
        aliases: dict[str, list[TopicAlias]] = {}
        rejections: dict[tuple[str, str], str] = {}
        for alias in alias_rows:
            aliases.setdefault(alias.topic_ref_id, []).append(
                TopicAlias(**{column: getattr(alias, column) for column in ALIAS_COLUMNS})
            )
            if alias.rejected_reason:
                rejections[(alias.topic_ref_id, alias.norm_key)] = alias.rejected_reason
        refs = [_ref_from_row(ref, aliases.get(ref.id, [])) for ref in ref_rows]
        stats = dict(row.stats)
        non_topical = list(stats.pop("non_topical_terms", []))
        universe = TopicUniverse(
            client_id=row.client_id,
            profile_version=row.profile_version,
            universe_hash=row.universe_hash,
            engine_version=row.engine_version,
            embedder_model_ids=row.embedder_model_ids,
            refs=refs,
            stats=TopicUniverseStats(**stats),
            warnings=list(row.warnings),
            built_at=row.built_at,
        )
        return universe, non_topical, rejections

    async def alias_decisions(self) -> dict[tuple[str, str], str]:
        # only a reviewer confirms a proposal or rejects with reason "user"; validator outcomes are recomputed
        statement = select(orm.TopicAlias.topic_ref_id, orm.TopicAlias.norm_key, orm.TopicAlias.status).where(
            orm.TopicAlias.workspace_id == self.scope.workspace_id,
            orm.TopicAlias.client_id == self.scope.client_id,
            (
                (orm.TopicAlias.origin == "llm_proposed") & (orm.TopicAlias.status == "confirmed")
                | (orm.TopicAlias.rejected_reason == USER_DECISION)
            ),
        )
        return {
            (ref_id, key): status for ref_id, key, status in (await self.session.execute(statement)).all()
        }


def _ref_from_row(row: orm.TopicRef, aliases: list[TopicAlias]) -> TopicRef:
    return TopicRef(
        id=row.id,
        client_id=row.client_id,
        profile_version=row.profile_version,
        kind=row.kind,
        source_item_id=row.source_item_id,
        label=row.label,
        definition=row.definition,
        aliases=aliases,
        negative_contexts=list(row.negative_contexts),
        parent_ids=list(row.parent_ids),
        offering_ids=list(row.offering_ids),
        funnel_stage=row.funnel_stage,
        facets=list(row.facets),
        core_entity_terms=list(row.core_entity_terms),
        locality_terms=list(row.locality_terms),
        weight=row.weight,
        status=row.status,
        expires_at=row.expires_at,
        fingerprint=row.fingerprint,
    )


class PreparedTextRepository(SharedRepository):
    async def get_many(self, video_ids: Sequence[str]) -> dict[str, PreparedVideoText]:
        if not video_ids:
            return {}
        statement = select(orm.VideoPreparedText).where(orm.VideoPreparedText.video_id.in_(list(video_ids)))
        rows = (await self.session.execute(statement)).scalars()
        return {
            row.video_id: PreparedVideoText(
                video_id=row.video_id,
                content_hash=row.content_hash,
                prepared_hash=row.prepared_hash,
                fields=PreparedFields(**row.fields),
                removed_spans=[RemovedSpan(**span) for span in row.removed_spans],
                script_profile=row.script_profile,
                code_mixed=row.code_mixed,
                lang_hint=row.lang_hint,
                quality_flags=list(row.quality_flags),
            )
            for row in rows
        }

    async def upsert_many(
        self, prepared: Sequence[PreparedVideoText], versions: Mapping[str, str], preparer: str, at: datetime
    ) -> None:
        rows = [
            {
                "video_id": item.video_id,
                "content_hash": item.content_hash,
                "prepared_hash": item.prepared_hash,
                "boilerplate_version": versions.get(item.video_id),
                "preparer_version": preparer,
                "fields": item.fields.model_dump(mode="json"),
                "removed_spans": [span.model_dump(mode="json") for span in item.removed_spans],
                "script_profile": item.script_profile,
                "code_mixed": item.code_mixed,
                "lang_hint": item.lang_hint,
                "quality_flags": list(item.quality_flags),
                "prepared_at": at,
            }
            for item in prepared
        ]
        table = orm.VideoPreparedText.__table__
        for chunk in chunked(rows, len(table.columns)):
            statement = insert(table).values(list(chunk))
            statement = statement.on_conflict_do_update(
                index_elements=["video_id"],
                set_={column: statement.excluded[column] for column in rows[0] if column != "video_id"},
            )
            await self.session.execute(statement)


class BoilerplateRepository(SharedRepository):
    async def get_many(self, channel_ids: Sequence[str]) -> dict[str, tuple[BoilerplateProfile, datetime]]:
        if not channel_ids:
            return {}
        statement = select(orm.ChannelBoilerplate).where(
            orm.ChannelBoilerplate.channel_id.in_(list(channel_ids))
        )
        return {
            row.channel_id: (
                BoilerplateProfile(
                    channel_id=row.channel_id,
                    version=row.boilerplate_version,
                    line_keys=frozenset(row.line_keys),
                    hashtags=frozenset(row.hashtags),
                    tags=frozenset(row.tags),
                    sample_size=row.sample_size,
                ),
                row.computed_at,
            )
            for row in (await self.session.execute(statement)).scalars()
        }

    async def upsert(self, profile: BoilerplateProfile, at: datetime) -> None:
        values = {
            "channel_id": profile.channel_id,
            "boilerplate_version": profile.version,
            "line_keys": sorted(profile.line_keys),
            "hashtags": sorted(profile.hashtags),
            "tags": sorted(profile.tags),
            "sample_size": profile.sample_size,
            "computed_at": at,
        }
        statement = insert(orm.ChannelBoilerplate).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["channel_id"],
            set_={key: statement.excluded[key] for key in values if key != "channel_id"},
        )
        await self.session.execute(statement)


class RefEmbeddingRepository(TenantRepository[orm.RefEmbedding]):
    model = orm.RefEmbedding

    async def get_many(self, model_id: str, fingerprints: Sequence[str]) -> dict[str, orm.RefEmbedding]:
        if not fingerprints:
            return {}
        statement = self._scoped(
            select(orm.RefEmbedding).where(
                orm.RefEmbedding.model_id == model_id,
                orm.RefEmbedding.ref_fingerprint.in_(list(fingerprints)),
            )
        )
        return {row.ref_fingerprint: row for row in (await self.session.execute(statement)).scalars()}

    async def upsert_many(self, rows: Sequence[Mapping[str, Any]]) -> None:
        table = orm.RefEmbedding.__table__
        scoped = [
            {**row, "workspace_id": self.scope.workspace_id, "client_id": self.scope.client_id}
            for row in rows
        ]
        for chunk in chunked(scoped, len(table.columns)):
            statement = insert(table).values(list(chunk))
            statement = statement.on_conflict_do_update(
                index_elements=["model_id", "ref_fingerprint", "client_id"],
                set_={
                    key: statement.excluded[key]
                    for key in ("vector", "bg_mu", "bg_q95", "bg_q99", "background_version")
                },
            )
            await self.session.execute(statement)


class TopicMatchRepository(TenantRepository[orm.TopicMatch]):
    model = orm.TopicMatch

    async def current_keys(self, video_ids: Sequence[str]) -> dict[str, set[tuple[str, str, str]]]:
        if not video_ids:
            return {}
        statement = self._scoped(
            select(
                orm.TopicMatch.video_id,
                orm.TopicMatch.universe_hash,
                orm.TopicMatch.prepared_hash,
                orm.TopicMatch.engine_version,
            ).where(orm.TopicMatch.video_id.in_(list(video_ids)), orm.TopicMatch.deleted_at.is_(None))
        )
        keys: dict[str, set[tuple[str, str, str]]] = {}
        for video_id, universe, prepared, engine in (await self.session.execute(statement)).all():
            keys.setdefault(video_id, set()).add((universe, prepared, engine))
        return keys

    async def replace_for_videos(
        self,
        video_ids: Sequence[str],
        matches: Sequence[TopicMatch],
        traces: Mapping[tuple[str, str], dict],
        observed: Mapping[str, datetime],
        expires: Mapping[str, datetime],
    ) -> None:
        if not video_ids:
            return
        await self.session.execute(
            delete(orm.TopicMatch).where(
                orm.TopicMatch.workspace_id == self.scope.workspace_id,
                orm.TopicMatch.client_id == self.scope.client_id,
                orm.TopicMatch.video_id.in_(list(video_ids)),
            )
        )
        rows = [
            {
                **match.model_dump(mode="json", exclude={"client_id", "matched_at"}),
                "workspace_id": self.scope.workspace_id,
                "client_id": match.client_id,
                "matched_at": match.matched_at,
                "trace": traces.get((match.video_id, match.topic_ref_id), {}),
                "api_data_observed_at": observed[match.video_id],
                "expires_at": expires[match.video_id],
            }
            for match in matches
        ]
        table = orm.TopicMatch.__table__
        for chunk in chunked(rows, len(table.columns)):
            await self.session.execute(insert(table).values(list(chunk)))

    async def list_for_videos(
        self, video_ids: Sequence[str] | None = None, *, universe_hash: str | None = None
    ) -> list[TopicMatch]:
        statement = self._scoped(select(orm.TopicMatch).where(orm.TopicMatch.deleted_at.is_(None)))
        if video_ids is not None:
            statement = statement.where(orm.TopicMatch.video_id.in_(list(video_ids)))
        if universe_hash is not None:
            statement = statement.where(orm.TopicMatch.universe_hash == universe_hash)
        statement = statement.order_by(orm.TopicMatch.video_id, orm.TopicMatch.rank)
        return [_match_from_row(row) for row in (await self.session.execute(statement)).scalars()]

    async def get_trace(self, video_id: str, ref_id: str) -> dict:
        statement = self._scoped(
            select(orm.TopicMatch.trace).where(
                orm.TopicMatch.video_id == video_id, orm.TopicMatch.topic_ref_id == ref_id
            )
        )
        return (await self.session.execute(statement)).scalar_one_or_none() or {}

    async def upsert_one(self, match: TopicMatch, trace: dict, observed: datetime, expires: datetime) -> None:
        values = {
            **match.model_dump(mode="json", exclude={"client_id", "matched_at"}),
            "workspace_id": self.scope.workspace_id,
            "client_id": match.client_id,
            "matched_at": match.matched_at,
            "trace": trace,
            "api_data_observed_at": observed,
            "expires_at": expires,
            "deleted_at": None,
        }
        statement = insert(orm.TopicMatch).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "video_id", "topic_ref_id", "universe_hash"],
            set_={
                key: statement.excluded[key]
                for key in values
                if key not in {"client_id", "video_id", "topic_ref_id", "universe_hash"}
            },
        )
        await self.session.execute(statement)

    async def adjudicated_none(self, video_ids: Sequence[str]) -> set[str]:
        if not video_ids:
            return set()
        statement = self._scoped(
            select(orm.TopicMatch.video_id).where(
                orm.TopicMatch.video_id.in_(list(video_ids)),
                orm.TopicMatch.trace["llm"]["verdict"].astext == "none",
            )
        )
        return set((await self.session.execute(statement)).scalars())

    async def soft_delete_refs(self, ref_ids: Sequence[str], at: datetime) -> int:
        if not ref_ids:
            return 0
        statement = (
            update(orm.TopicMatch)
            .where(
                orm.TopicMatch.workspace_id == self.scope.workspace_id,
                orm.TopicMatch.client_id == self.scope.client_id,
                orm.TopicMatch.topic_ref_id.in_(list(ref_ids)),
                orm.TopicMatch.deleted_at.is_(None),
            )
            .values(deleted_at=at)
        )
        return (await self.session.execute(statement)).rowcount


def _match_from_row(row: orm.TopicMatch) -> TopicMatch:
    return TopicMatch(
        client_id=row.client_id,
        video_id=row.video_id,
        topic_ref_id=row.topic_ref_id,
        topic_kind=row.topic_kind,
        profile_version=row.profile_version,
        universe_hash=row.universe_hash,
        prepared_hash=row.prepared_hash,
        engine_version=row.engine_version,
        decision=row.decision,
        lexical_score=row.lexical_score,
        semantic_score=row.semantic_score,
        combined_score=row.combined_score,
        method=row.method,
        confidence=row.confidence,
        is_primary=row.is_primary,
        rank=row.rank,
        implied_from=row.implied_from,
        evidence=row.evidence,
        review_reasons=list(row.review_reasons),
        degraded=list(row.degraded),
        explanation=row.explanation,
        matched_at=row.matched_at,
    )


class DirectnessRepository(TenantRepository[orm.AnswerDirectness]):
    model = orm.AnswerDirectness

    async def replace_for_videos(
        self,
        video_ids: Sequence[str],
        rows: Sequence[AnswerDirectness],
        fingerprints: Mapping[str, str],
        version: str,
        at: datetime,
        observed: Mapping[str, datetime],
        expires: Mapping[str, datetime],
    ) -> None:
        if not video_ids:
            return
        await self.session.execute(
            delete(orm.AnswerDirectness).where(
                orm.AnswerDirectness.workspace_id == self.scope.workspace_id,
                orm.AnswerDirectness.client_id == self.scope.client_id,
                orm.AnswerDirectness.video_id.in_(list(video_ids)),
            )
        )
        for item in rows:
            await self.upsert(
                item,
                fingerprints[item.buyer_question_ref_id],
                version,
                at,
                observed[item.video_id],
                expires[item.video_id],
            )

    async def upsert(
        self,
        item: AnswerDirectness,
        ref_fingerprint: str,
        version: str,
        at: datetime,
        observed: datetime,
        expires: datetime,
    ) -> None:
        values = {
            "workspace_id": self.scope.workspace_id,
            "client_id": item.client_id,
            "video_id": item.video_id,
            "bq_ref_id": item.buyer_question_ref_id,
            "universe_hash": item.universe_hash,
            "prepared_hash": item.prepared_hash,
            "ref_fingerprint": ref_fingerprint,
            "directness_version": version,
            "score": item.score,
            "level": item.level.value,
            "components": item.components.model_dump(),
            "facets_covered": [facet.value for facet in item.facets_covered],
            "facets_missing": [facet.value for facet in item.facets_missing],
            "method": item.method,
            "explanation": item.explanation,
            "computed_at": at,
            "api_data_observed_at": observed,
            "expires_at": expires,
        }
        statement = insert(orm.AnswerDirectness).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "video_id", "bq_ref_id"],
            set_={
                key: statement.excluded[key]
                for key in values
                if key not in {"client_id", "video_id", "bq_ref_id"}
            },
        )
        await self.session.execute(statement)

    async def list_for_videos(self, video_ids: Sequence[str]) -> list[AnswerDirectness]:
        statement = self._scoped(
            select(orm.AnswerDirectness)
            .where(orm.AnswerDirectness.video_id.in_(list(video_ids)))
            .order_by(orm.AnswerDirectness.video_id, orm.AnswerDirectness.bq_ref_id)
        )
        return [
            AnswerDirectness(
                client_id=row.client_id,
                video_id=row.video_id,
                buyer_question_ref_id=row.bq_ref_id,
                universe_hash=row.universe_hash,
                prepared_hash=row.prepared_hash,
                score=row.score,
                level=row.level,
                components=row.components,
                facets_covered=list(row.facets_covered),
                facets_missing=list(row.facets_missing),
                method=row.method,
                explanation=row.explanation,
            )
            for row in (await self.session.execute(statement)).scalars()
        ]


class AdjudicationRepository(TenantRepository[orm.LlmAdjudication]):
    model = orm.LlmAdjudication

    async def get_many(self, keys: Sequence[str]) -> dict[str, dict]:
        if not keys:
            return {}
        statement = self._scoped(
            select(orm.LlmAdjudication.cache_key, orm.LlmAdjudication.verdict).where(
                orm.LlmAdjudication.cache_key.in_(list(keys))
            )
        )
        return {key: verdict for key, verdict in (await self.session.execute(statement)).all()}

    async def put_many(self, rows: Sequence[Mapping[str, Any]]) -> None:
        table = orm.LlmAdjudication.__table__
        scoped = [
            {**row, "workspace_id": self.scope.workspace_id, "client_id": self.scope.client_id}
            for row in rows
        ]
        for chunk in chunked(scoped, len(table.columns)):
            await self.session.execute(insert(table).values(list(chunk)).on_conflict_do_nothing())


class OverrideRepository(TenantRepository[orm.TopicMatchOverride]):
    model = orm.TopicMatchOverride

    async def upsert(self, values: Mapping[str, Any]) -> None:
        row = {
            **values,
            "workspace_id": self.scope.workspace_id,
            "client_id": self.scope.client_id,
            "stale": False,
        }
        statement = insert(orm.TopicMatchOverride).values(**row)
        statement = statement.on_conflict_do_update(
            index_elements=["client_id", "video_id", "topic_ref_id"],
            set_={
                key: statement.excluded[key]
                for key in row
                if key not in {"client_id", "video_id", "topic_ref_id"}
            },
        )
        await self.session.execute(statement)

    async def list_for_videos(self, video_ids: Sequence[str]) -> list[orm.TopicMatchOverride]:
        if not video_ids:
            return []
        statement = self._scoped(
            select(orm.TopicMatchOverride).where(orm.TopicMatchOverride.video_id.in_(list(video_ids)))
        )
        return list((await self.session.execute(statement)).scalars())

    async def mark_stale(self, content_hashes: Mapping[str, str]) -> int:
        stale = 0
        for video_id, content_hash in content_hashes.items():
            statement = (
                update(orm.TopicMatchOverride)
                .where(
                    orm.TopicMatchOverride.workspace_id == self.scope.workspace_id,
                    orm.TopicMatchOverride.client_id == self.scope.client_id,
                    orm.TopicMatchOverride.video_id == video_id,
                    orm.TopicMatchOverride.content_hash_at_review != content_hash,
                    orm.TopicMatchOverride.stale.is_(False),
                )
                .values(stale=True)
            )
            stale += (await self.session.execute(statement)).rowcount
        return stale


class ReviewRepository(TenantRepository[orm.TopicReviewItem]):
    model = orm.TopicReviewItem

    async def open_items(self, *, limit: int = 100) -> list[orm.TopicReviewItem]:
        statement = self._scoped(
            select(orm.TopicReviewItem)
            .where(orm.TopicReviewItem.status == "open")
            .order_by(orm.TopicReviewItem.priority.desc(), orm.TopicReviewItem.video_id)
            .limit(limit)
        )
        return list((await self.session.execute(statement)).scalars())

    async def sync_open(self, video_ids: Sequence[str], items: Sequence[Mapping[str, Any]]) -> None:
        # open items for rematched videos are replaced; resolved history stays
        if video_ids:
            await self.session.execute(
                delete(orm.TopicReviewItem).where(
                    orm.TopicReviewItem.workspace_id == self.scope.workspace_id,
                    orm.TopicReviewItem.client_id == self.scope.client_id,
                    orm.TopicReviewItem.video_id.in_(list(video_ids)),
                    orm.TopicReviewItem.status == "open",
                )
            )
        resolved = await self._resolved_pairs(video_ids)
        rows = [
            {**item, "workspace_id": self.scope.workspace_id, "client_id": self.scope.client_id}
            for item in items
            if (item["video_id"], item["topic_ref_id"]) not in resolved
        ]
        table = orm.TopicReviewItem.__table__
        for chunk in chunked(rows, len(table.columns)):
            await self.session.execute(insert(table).values(list(chunk)))

    async def add_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        rows = [
            {**item, "workspace_id": self.scope.workspace_id, "client_id": self.scope.client_id}
            for item in items
        ]
        table = orm.TopicReviewItem.__table__
        for chunk in chunked(rows, len(table.columns)):
            await self.session.execute(insert(table).values(list(chunk)))

    async def reviewed_pairs(self, video_ids: Sequence[str]) -> set[tuple[str, str]]:
        return await self._resolved_pairs(video_ids)

    async def _resolved_pairs(self, video_ids: Sequence[str]) -> set[tuple[str, str]]:
        if not video_ids:
            return set()
        statement = self._scoped(
            select(orm.TopicReviewItem.video_id, orm.TopicReviewItem.topic_ref_id).where(
                orm.TopicReviewItem.video_id.in_(list(video_ids)), orm.TopicReviewItem.status != "open"
            )
        )
        return {(video_id, ref_id) for video_id, ref_id in (await self.session.execute(statement)).all()}

    async def resolve(self, video_id: str, ref_id: str, resolution: str, at: datetime) -> int:
        statement = (
            update(orm.TopicReviewItem)
            .where(
                orm.TopicReviewItem.workspace_id == self.scope.workspace_id,
                orm.TopicReviewItem.client_id == self.scope.client_id,
                orm.TopicReviewItem.video_id == video_id,
                orm.TopicReviewItem.topic_ref_id == ref_id,
                orm.TopicReviewItem.status == "open",
            )
            .values(status="resolved", resolution=resolution, resolved_at=at)
        )
        return (await self.session.execute(statement)).rowcount

    async def raise_priority(self, video_ids: Sequence[str], impact: float, reason: str) -> int:
        if not video_ids:
            return 0
        statement = self._scoped(
            select(orm.TopicReviewItem).where(
                orm.TopicReviewItem.video_id.in_(list(video_ids)), orm.TopicReviewItem.status == "open"
            )
        )
        items = list((await self.session.execute(statement)).scalars())
        for item in items:
            # stored priority is impact x uncertainty with impact 1, so the uncertainty factor is kept
            item.priority = max(item.priority, item.priority * impact)
            if reason not in item.reasons:
                item.reasons = [*item.reasons, reason]
        await self.session.flush()
        return len(items)
